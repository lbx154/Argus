"""literary_editor intake — consumes the shared Task Envelope (fifth consumer).

Unlike the genre verticals, the editor's ``mode`` (rewrite/expand/polish/
proofread/critique) is itself the task type — and every one of these is a Task
Envelope mode that the shared contract already REQUIRES to carry a source
reference. So intake reuses that guarantee and only adds the editor-specific
check that ``mode`` is an editing mode. ``form`` is passed through: the editor
edits text of any genre.
"""
from __future__ import annotations

from typing import Any

from ...literary.task_envelope import normalize_envelope
from .edit_ops import EDITOR_MODES

_SOURCE_ROLES = frozenset({"source_text", "prior_state"})


class EditorIntakeError(ValueError):
    """Raised when a task envelope cannot be consumed as an editing brief."""


def brief_from_envelope(env: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``env`` (which enforces a source ref for editing modes) and derive
    the private editing brief: the mode, the goal, the must-keep segments, and
    whether new facts may be introduced."""
    env = normalize_envelope(env)
    if env["mode"] not in EDITOR_MODES:
        raise EditorIntakeError(
            f"literary_editor handles editing modes {sorted(EDITOR_MODES)}, "
            f"not mode {env['mode']!r}"
        )
    out_req = env.get("output_requirements") or {}
    refs = env.get("reference_inputs", [])
    source = next((r for r in refs if r.get("role") in _SOURCE_ROLES), None)
    if source is None:  # defensive: the envelope rule should already guarantee this
        raise EditorIntakeError(
            f"mode={env['mode']!r} edits existing text but no source reference_input "
            f"was provided"
        )
    return {
        "task_id": env["task_id"],
        "mode": env["mode"],
        "language": env["language"],
        "form": env["form"],
        "goal": env["intent"],
        "must_keep": list(out_req.get("must_not_break", [])),
        "allow_new_facts": bool(out_req.get("allow_new_facts",
                                            env["mode"] in {"expand", "rewrite"})),
        "source_ref": source.get("ref"),
        "reference_inputs": list(refs),
    }


__all__ = ["EditorIntakeError", "brief_from_envelope"]
