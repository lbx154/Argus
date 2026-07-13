"""modern_poetry intake — consumes the shared Task Envelope (third consumer).

Free verse in zh OR en. Rejects narrative forms and classical-poetry forms (route
those to fiction_writing / classical_poetry). Declared hard constraints (line
count, banned words, language) are carried into the brief for the FORM layer.
"""
from __future__ import annotations

from typing import Any

from ...literary.task_envelope import normalize_envelope

#: Modern-poetry forms (zh names + english aliases).
MODERN_FORMS: frozenset[str] = frozenset({
    "free_verse", "prose_poem", "lyric", "现代诗", "自由诗", "散文诗", "抒情诗",
})


class ModernPoetryIntakeError(ValueError):
    """Raised when a task envelope cannot be consumed as a modern-poetry brief."""


def brief_from_envelope(env: dict[str, Any]) -> dict[str, Any]:
    env = normalize_envelope(env)
    if env["form"] not in MODERN_FORMS:
        raise ModernPoetryIntakeError(
            f"modern_poetry does not handle form {env['form']!r} "
            f"(expected one of {sorted(MODERN_FORMS)})"
        )
    out_req = env.get("output_requirements") or {}
    # the machine FORM spec: only declared hard constraints
    form_spec = {
        "language": env["language"],
        "line_count": out_req.get("line_count"),
        "min_lines": out_req.get("min_lines"),
        "max_lines": out_req.get("max_lines"),
        "banned_words": list(out_req.get("banned_words", [])),
    }
    return {
        "task_id": env["task_id"],
        "language": env["language"],
        "form": env["form"],
        "mode": env["mode"],
        "intent": env["intent"],
        "form_spec": {k: v for k, v in form_spec.items() if v not in (None, [])},
        "constraints": list(env.get("constraints", [])),
        "reference_inputs": list(env.get("reference_inputs", [])),
    }


__all__ = ["MODERN_FORMS", "ModernPoetryIntakeError", "brief_from_envelope"]
