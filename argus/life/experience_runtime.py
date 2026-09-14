"""Expose experience lifecycle tools during the host-owned provider call."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from ..advisor.runtime import caller_role
from .experience_tools import ExperienceBridge, ExperienceToolService

EXTENSION = str(Path(__file__).with_name("experience_extension.mjs"))
log = logging.getLogger(__name__)


@contextmanager
def experience_run(ctx: Any) -> Iterator[None]:
    options = ctx.options
    role = caller_role(ctx.run_label)
    root = ctx.usage_project_root
    native = ctx.backend._backend_name == "pi"
    if (root is None or role is None or options.disable_tools
            or (not native and options.sandbox_mode == "read-only")):
        yield
        return
    try:
        service = ExperienceToolService(
            Path(root), role=role, parent_call_id=ctx.call_id,
            # A scoped memory API is independent of workspace write tools. The
            # Reviewer remains read-only inside the service as well as discovery.
            writable=role != "reviewer",
            workspace=Path(options.working_dir) if options.working_dir else Path(root),
            redact=lambda text: _redact_for_call(ctx, text),
        )
    except (OSError, ValueError):
        log.warning("experience tools unavailable for this project context")
        yield
        return
    names = ["search_experiences", "get_experience"]
    if service.writable:
        names.extend(["revise_experience", "retract_experience"])
    with ExperienceBridge(service) as bridge:
        ctx.options = replace(
            options,
            trusted_extensions=list(dict.fromkeys([*(options.trusted_extensions or []), *([EXTENSION] if native else [])])),
            trusted_tool_names=list(dict.fromkeys([*(options.trusted_tool_names or []), *(names if native else [])])),
            extension_env={**(options.extension_env or {}), **bridge.environment,
                           "ARGUS_PLUGIN_EXPERIENCE_WRITABLE": "1" if service.writable else "0"},
            force_safe_mode=options.force_safe_mode or options.sandbox_mode == "read-only",
        )
        tool = ", ".join(names) if native else "python -m argus_skill.tools.experience"
        ctx.prompt += (
            "\n\nPrior mission experiences: " + tool + " provides project-scoped advisory memory. "
            "Search and inspect the current id, revision, scope and evidence before reuse. "
            "Success is one scoped observation; a failure does not establish impossibility. "
        )
        if service.writable:
            ctx.prompt += (
                "When new evidence corrects an existing interpretation, revise its existing id with "
                "expected_revision, evidence_refs, reason and changes; retract an invalid capsule with "
                "expected_revision, evidence_refs and reason. A stale revision requires a fresh get. "
                "Evidence refs must be existing relative files under workspace: or state:, at most 16 "
                "complete text files and 32 KiB combined. The host verifies file scope and content hashes, "
                "not whether an interpretation follows from the evidence. Inspect or extract a bounded "
                "evidence file yourself when the original artifact is larger. "
                "Similarity alone does not justify rewriting evidence or merging conclusions. "
                "The CLI uses search QUERY, get ID, revise ID --expected-revision N --evidence-ref REF "
                "--reason TEXT --changes JSON, or retract ID with the same evidence/revision/reason flags. "
            )
        yield


def _redact_for_call(ctx: Any, text: str) -> str:
    from ..core.secret_guard import redact_secrets_text

    return redact_secrets_text(text, known_values=getattr(ctx.backend, "_known_secret_values", ()))
