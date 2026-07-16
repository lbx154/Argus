"""Deterministic TEMPORAL / age-consistency check for a fiction ``story_state``.

Review-side ONLY. The safe-patch engine (:mod:`.state`) stays schema + referential-
integrity: it now STORES ``birth_year``/``age``/``world_clock``/timeline ``year``,
but it never does arithmetic over them. Cross-field temporal reasoning
(age == current_year − birth_year; nobody is born after the story's clock; a later
timeline ``order`` may not carry an earlier ``year``) is a DERIVED consistency fact
spanning meta + characters + timeline — so, like ``modern_poetry``'s ``check_form``,
it lives OUTSIDE the mutation engine and is consumed by the review stage as a
blocking check. This is exactly the class of bug (a character 34 in year 2042 who
earned a license at age 20 = year 2028, before the system existed) that had no
home before: the fields did not exist, so nothing could catch it.

Never imported by :mod:`.state`.
"""
from __future__ import annotations

from typing import Any

#: The blocking continuity type this check emits (also in FICTION_CONTINUITY_TYPES).
TEMPORAL_FINDING_TYPE = "temporal_consistency"


class TemporalError(ValueError):
    """Raised when a story_state is structurally unfit for the temporal check."""


def _finding(detail: str, character_id: str | None = None) -> dict[str, Any]:
    return {
        "type": TEMPORAL_FINDING_TYPE,
        "severity": "major",
        "blocking": True,
        "character_id": character_id,
        "detail": detail,
    }


def check_temporal_consistency(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return BLOCKING temporal contradictions in ``state``.

    Silent (returns ``[]``) whenever the numbers a given check needs are absent —
    the check never manufactures an error from missing data, so a state that opts
    out of temporal tracking is simply never flagged.
    """
    if not isinstance(state, dict):
        raise TemporalError("story_state must be an object")
    findings: list[dict[str, Any]] = []
    meta = state.get("meta") or {}
    clock = meta.get("world_clock") or {}
    current_year = clock.get("current_year")

    for cid, char in (state.get("characters") or {}).items():
        if not isinstance(char, dict):
            continue
        name = char.get("name", cid)
        age = char.get("age")
        birth = char.get("birth_year")
        if isinstance(age, int) and age < 0:
            findings.append(_finding(f"{name}: age {age} is negative", cid))
        if isinstance(birth, int) and isinstance(current_year, int) and birth > current_year:
            findings.append(_finding(
                f"{name}: birth_year {birth} is after the story's current_year "
                f"{current_year} (born in the future)", cid))
        if isinstance(age, int) and isinstance(birth, int) and isinstance(current_year, int):
            expected = current_year - birth
            if expected != age:
                findings.append(_finding(
                    f"{name}: stated age {age} contradicts current_year {current_year} "
                    f"− birth_year {birth} = {expected}", cid))

    # timeline entries carrying BOTH order and year must not invert
    dated = [t for t in (state.get("timeline") or [])
             if isinstance(t, dict) and isinstance(t.get("order"), int)
             and isinstance(t.get("year"), int)]
    dated.sort(key=lambda t: t["order"])
    for earlier, later in zip(dated, dated[1:]):
        if later["year"] < earlier["year"]:
            findings.append(_finding(
                f"timeline: {later.get('id')!r} (order {later['order']}, year "
                f"{later['year']}) comes after {earlier.get('id')!r} (order "
                f"{earlier['order']}, year {earlier['year']}) but carries an earlier year"))
    return findings


__all__ = ["TEMPORAL_FINDING_TYPE", "TemporalError", "check_temporal_consistency"]
