"""prose intake — consumes the shared Task Envelope (fourth consumer)."""
from __future__ import annotations

from typing import Any

from ...literary.task_envelope import normalize_envelope

#: Prose forms this vertical handles (zh names + english aliases).
PROSE_FORMS: frozenset[str] = frozenset({
    "lyric_essay", "narrative_essay", "essay", "memoir",
    "散文", "抒情散文", "叙事散文", "随笔", "回忆",
})


class ProseIntakeError(ValueError):
    """Raised when a task envelope cannot be consumed as a prose brief."""


def brief_from_envelope(env: dict[str, Any]) -> dict[str, Any]:
    env = normalize_envelope(env)
    if env["form"] not in PROSE_FORMS:
        raise ProseIntakeError(
            f"prose does not handle form {env['form']!r} "
            f"(expected one of {sorted(PROSE_FORMS)})"
        )
    out_req = env.get("output_requirements") or {}
    spec = {
        "language": env["language"],
        "min_paragraphs": out_req.get("min_paragraphs"),
        "max_paragraphs": out_req.get("max_paragraphs"),
        "banned_words": list(out_req.get("banned_words", [])),
    }
    return {
        "task_id": env["task_id"],
        "language": env["language"],
        "form": env["form"],
        "mode": env["mode"],
        "intent": env["intent"],
        "spec": {k: v for k, v in spec.items() if v not in (None, [])},
        "constraints": list(env.get("constraints", [])),
        "reference_inputs": list(env.get("reference_inputs", [])),
    }


__all__ = ["PROSE_FORMS", "ProseIntakeError", "brief_from_envelope"]
