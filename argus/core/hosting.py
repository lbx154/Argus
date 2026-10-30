"""The single boundary to optional client and service extensions.

Each entry-point provider answers the questions below. With no provider,
Argus uses its ordinary local behavior. Provider load errors propagate.
"""
from __future__ import annotations

import functools
from contextlib import nullcontext
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypedDict


class WorkPause(TypedDict):
    reason: str
    since: float


NO_HOSTING = SimpleNamespace(
    provider_active=lambda backend, env=None: False,
    model_options=lambda model, effort, *, env=None: (model, effort),
    auto_model=lambda backend, env: "",
    effort_override=lambda env: None,
    supports_reasoning_effort=lambda model: False,
    auth_source=lambda env: "",
    apply_provider=lambda env: env,
    redactions=lambda: (),
    copilot_session_store=lambda: None,
    provider_setup=lambda: nullcontext(),
    provider_configured=lambda values: values,
    prepare_call=lambda backend, options: options,
    finish_call=lambda backend, result, **kwargs: result,
    failure_cause=lambda diagnostic: None,
    failure_review=lambda cause, diagnostic, exit_code: None,
    operator_update=lambda **kwargs: "",
    wording=lambda kind: None,
    failure_kind=lambda diagnostic: None,
    failure_message=lambda diagnostic, chinese: "",
    is_service_failure=lambda diagnostic: True,
    usage_pricing=lambda provider: None,
    model_change_error=lambda: "",
    prompt_completion_enabled=lambda: True,
    sibling_libraries_enabled=lambda: True,
    work_pause=lambda root: None,
    manages_verticals=lambda env: False,
    capture_runtime_call=lambda ctx, options: nullcontext(),
    daemon_launch_payload=lambda config: None,
    arm_daemon_launch=lambda payload, config: None,
    prepare_daemon_fork=lambda config: None,
    wait_daemon_claim=lambda handoff: None,
    complete_daemon_fork=lambda handoff, *, forked=True: None,
    plugin_setup_actions=lambda: None,
    plugin_middleware=lambda name: None,
)


@functools.cache
def hosting() -> ModuleType | SimpleNamespace:
    """The installed client provider, or ordinary local behavior."""
    for entry in entry_points(group="argus.hosting.client"):
        return entry.load()
    return NO_HOSTING


@functools.cache
def hosted_runtime() -> ModuleType | SimpleNamespace:
    """The installed service provider, or ordinary local behavior."""
    for entry in entry_points(group="argus.hosting"):
        return entry.load()
    return NO_HOSTING


def work_pause(root: str | Path) -> WorkPause | None:
    """The service's reason and timestamp while work is paused."""
    return hosted_runtime().work_pause(root)
