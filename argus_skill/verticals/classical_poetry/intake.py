"""classical_poetry intake adapter: shared Task Envelope -> poetry brief.

The poetry end of the Task-Envelope loop — the SECOND vertical to consume the
shared contract, proving it generalizes beyond narrative. The derived brief is
PRIVATE to poetry (诗体/韵/句数 are prosody concepts fiction/prose would not share).

Two vertical-local semantic checks the shared schema cannot express:
* classical Chinese poetry is written in Chinese — a ``language`` other than
  ``zh`` is rejected loudly at intake;
* ``form`` must be a classical-poetry form (绝句/律诗/古体/词, or an english alias),
  not a narrative form — a mis-routed short_story fails here.
"""
from __future__ import annotations

from typing import Any

from ...literary.task_envelope import normalize_envelope

#: Classical-poetry forms this vertical handles (zh names + english aliases).
POETRY_FORMS: frozenset[str] = frozenset({
    "绝句", "律诗", "五言绝句", "七言绝句", "五言律诗", "七言律诗",
    "古体诗", "词", "quatrain", "regulated_verse", "jueju", "lushi", "ci",
})

#: Forms that are 近体诗 (bound by the prosody 谱) vs 古体/词 (rhyme-only gating).
_JINTI_FORMS: frozenset[str] = frozenset({
    "绝句", "律诗", "五言绝句", "七言绝句", "五言律诗", "七言律诗",
    "quatrain", "regulated_verse", "jueju", "lushi",
})


class PoetryIntakeError(ValueError):
    """Raised when a task envelope cannot be consumed as a classical-poetry brief."""


def brief_from_envelope(env: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``env`` and derive poetry's private brief.

    The shared contract validates the envelope first; then poetry's own rules
    (Chinese language, a classical-poetry form) run.
    """
    env = normalize_envelope(env)
    if env["language"] != "zh":
        raise PoetryIntakeError(
            f"classical_poetry writes in Chinese; language={env['language']!r} "
            f"is not supported (route non-zh verse to modern_poetry)"
        )
    if env["form"] not in POETRY_FORMS:
        raise PoetryIntakeError(
            f"classical_poetry does not handle form {env['form']!r} "
            f"(expected one of {sorted(POETRY_FORMS)})"
        )
    out_req = env.get("output_requirements") or {}
    return {
        "task_id": env["task_id"],
        "language": env["language"],
        "form": env["form"],
        "is_jinti": env["form"] in _JINTI_FORMS,
        "mode": env["mode"],
        "rhyme_target": out_req.get("rhyme_target"),   # e.g. a 平声 韵部
        "yan": out_req.get("yan"),                     # 5 or 7
        "intent": env["intent"],
        "constraints": list(env.get("constraints", [])),
        "reference_inputs": list(env.get("reference_inputs", [])),
    }


__all__ = ["POETRY_FORMS", "PoetryIntakeError", "brief_from_envelope"]
