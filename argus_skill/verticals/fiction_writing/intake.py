"""fiction_writing intake adapter: shared Task Envelope -> fiction creative_brief.

This is the fiction end of the Task-Envelope closed loop. The envelope is the
SHARED cross-vertical task contract (:mod:`argus_skill.literary.task_envelope`);
fiction is its first real consumer. The ``creative_brief`` derived here stays
PRIVATE to fiction (viewpoint/tense/market_style/genre are narrative concepts a
poem or essay would not share).

The adapter refuses a task whose ``form`` is not a narrative-fiction form (e.g.
a poetry ``quatrain``), so a mis-routed task fails LOUDLY at intake rather than
silently producing an incoherent brief.
"""
from __future__ import annotations

from typing import Any

from ...literary.task_envelope import normalize_envelope
from .profiles import FictionProfileError, resolve_profile

#: The narrative-prose forms fiction_writing handles. A ``form`` outside this set
#: (e.g. a poetry/essay form) is rejected at intake.
FICTION_FORMS: frozenset[str] = frozenset(
    {"short_story", "chapter", "scene", "novella", "novel", "flash_fiction"}
)


class FictionIntakeError(ValueError):
    """Raised when a task envelope cannot be consumed as a fiction brief."""


def brief_from_envelope(env: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``env`` and derive fiction's private ``creative_brief`` dict.

    The envelope is validated/normalized by the shared contract first; then the
    fiction-specific check (``form`` is a narrative form) runs. Viewpoint, tense
    and market_style are read from ``output_requirements`` when the operator
    pinned them, else defaulted (third-limited past, genre market) — the intake
    reviewer later confirms nothing critical was left implicit.
    """
    env = normalize_envelope(env)
    if env["form"] not in FICTION_FORMS:
        raise FictionIntakeError(
            f"fiction_writing does not handle form {env['form']!r} "
            f"(expected one of {sorted(FICTION_FORMS)})"
        )
    out_req = env.get("output_requirements") or {}
    try:
        profile = resolve_profile(out_req.get("profile"))
    except FictionProfileError as exc:
        raise FictionIntakeError(str(exc)) from exc
    return {
        "task_id": env["task_id"],
        "profile": profile,
        "language": env["language"],
        "form": env["form"],
        "mode": env["mode"],
        "genre": env.get("genre_profile") or "unspecified",
        "market_style": out_req.get("market_style", "genre"),
        "length": env.get("target_length"),
        "viewpoint": out_req.get("viewpoint", "third_limited"),
        "tense": out_req.get("tense", "past"),
        "constraints": list(env.get("constraints", [])),
        "reference_inputs": list(env.get("reference_inputs", [])),
    }


__all__ = ["FICTION_FORMS", "FictionIntakeError", "brief_from_envelope"]
