"""Single actual-provider-call hook; never widens the caller's builtin tools."""
from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from .config import AdvisorConfigError, load_advisor_config
from .prompts import advisor_prompt
from .service import AdvisorCallContext, AdvisorService
from .transport import AdvisorBridge

log = logging.getLogger(__name__)
EXTENSION = str(Path(__file__).with_name("pi_extension.mjs"))


def caller_role(run_label: str) -> str | None:
    label = run_label.lower()
    for role in ("planner", "engineer", "reviewer", "manager"):
        if label == role or label.startswith((role + ".", role + "-", role + "_")):
            return role
    if label.startswith(("simple", "chat", "router", "vertical", "stage", "domain")):
        return "manager"
    return None


@contextmanager
def advisor_run(ctx: Any) -> Iterator[None]:
    options = ctx.options
    role = caller_role(ctx.run_label)
    root = ctx.usage_project_root
    if root is None or role is None or options.disable_tools or not options.working_dir:
        yield
        return
    try:
        config = load_advisor_config(root)
    except AdvisorConfigError:
        log.warning("Advisor is unavailable because project configuration is invalid")
        yield
        return
    if not config.enabled:
        yield
        return
    backend = ctx.backend._backend_name
    native = backend in {"pi", "copilot"}
    # Other execution backends can use the same CLI protocol through their
    # existing shell tool. Read-only roles never gain shell as a fallback.
    if not native and options.sandbox_mode == "read-only":
        yield
        return

    def interrupt() -> str | None:
        for provider in (
            options.external_interrupt_reason_provider,
            getattr(ctx.backend, "_default_interrupt_reason_provider", None),
        ):
            if provider:
                reason = provider()
                if reason:
                    return str(reason)
        return None

    service = AdvisorService(AdvisorCallContext(
        project_root=Path(root), workspace=Path(options.working_dir), caller_role=role,
        parent_call_id=ctx.call_id, mission_id=ctx.usage_mission_id,
        global_root=ctx.usage_global_root, interrupt_reason_provider=interrupt,
    ), config=config)
    with AdvisorBridge(service) as bridge, ExitStack() as resources:
        extra_args = list(options.extra_args or [])
        tool_name = "consult_advisor"
        if backend == "copilot":
            from .copilot import TOOL_NAME, advisor_mcp_args

            tool_name = TOOL_NAME
            extra_args.extend(resources.enter_context(advisor_mcp_args(bridge.environment)))
        ctx.options = replace(
            options,
            trusted_extensions=list(dict.fromkeys([*(options.trusted_extensions or []), *([EXTENSION] if backend == "pi" else [])])),
            trusted_tool_names=list(dict.fromkeys([*(options.trusted_tool_names or []), *([tool_name] if native else [])])),
            extension_env={**(options.extension_env or {}), **bridge.environment},
            extra_args=extra_args or None,
            force_safe_mode=options.force_safe_mode or options.sandbox_mode == "read-only",
        )
        ctx.prompt += advisor_prompt(role, native=native, tool_name=tool_name)
        yield
