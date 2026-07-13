"""prose STRUCTURE layer — the honest, thin deterministic checks.

Prose has no meter and no fixed form, so this does NOT judge prose quality. It
checks two mechanical things:

1. **prose_state structure** — the private planning object must carry the fields
   this vertical is built around (narrative_center / observation_subject /
   factual_anchors / memory_boundary / paragraph_movement / ending_strategy). A
   plan missing them is a structural finding — the writer cannot skip declaring
   what the piece observes and where fact ends and memory begins.
2. **draft hard constraints** — declared language, paragraph count bounds, and a
   banned-cliché list.

Whether the draft HONORS its declared memory_boundary (no invented facts), whether
observation is concrete, whether paragraphs actually move — all LIVE-reviewer.
"""
from __future__ import annotations

import re
from typing import Any

#: The fields a prose_state must declare (the vertical's private craft state).
PROSE_STATE_FIELDS: tuple[str, ...] = (
    "narrative_center", "observation_subject", "factual_anchors",
    "memory_boundary", "paragraph_movement", "ending_strategy",
)

#: Machine-decidable prose finding types (structure + hard constraints only).
STRUCTURE_FINDING_TYPES: frozenset[str] = frozenset({
    "structure", "language", "paragraph_count", "banned_word", "empty",
})


class StructureError(ValueError):
    """Raised when a prose_state or spec is malformed at the wrong layer."""


def _finding(ftype: str, detail: str, where: str | None = None) -> dict[str, Any]:
    return {"type": ftype, "severity": "blocking", "location": where, "detail": detail}


def validate_prose_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return blocking findings for any required prose_state field missing/empty."""
    findings: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        raise StructureError("prose_state must be an object")
    for field in PROSE_STATE_FIELDS:
        val = state.get(field)
        if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
            findings.append(_finding("structure", f"prose_state missing {field!r}", field))
    return findings


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _han_ratio(s: str) -> float:
    non_ws = [c for c in s if not c.isspace()]
    if not non_ws:
        return 0.0
    han = sum(1 for c in non_ws if "一" <= c <= "鿿")
    return han / len(non_ws)


def check_draft(text: str, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return blocking findings where ``text`` violates the declared ``spec``.

    ``spec`` keys (all optional): ``language`` ('zh'|'en'), ``min_paragraphs``,
    ``max_paragraphs`` (int), ``banned_words`` (list[str]).
    """
    spec = spec or {}
    if not isinstance(spec, dict):
        raise StructureError("prose spec must be an object")
    findings: list[dict[str, Any]] = []
    paras = _paragraphs(text)
    if not paras:
        findings.append(_finding("empty", "draft is empty (no paragraphs)"))
        return findings
    if spec.get("min_paragraphs") is not None and len(paras) < int(spec["min_paragraphs"]):
        findings.append(_finding(
            "paragraph_count", f"{len(paras)} paragraphs, below min {spec['min_paragraphs']}"))
    if spec.get("max_paragraphs") is not None and len(paras) > int(spec["max_paragraphs"]):
        findings.append(_finding(
            "paragraph_count", f"{len(paras)} paragraphs, above max {spec['max_paragraphs']}"))
    lang = spec.get("language")
    if lang == "zh" and _han_ratio(text) < 0.5:
        findings.append(_finding("language", "declared zh but not predominantly Han script"))
    if lang == "en" and _han_ratio(text) > 0.2:
        findings.append(_finding("language", "declared en but contains substantial Han script"))
    for w in (spec.get("banned_words") or []):
        if w and w in text:
            findings.append(_finding("banned_word", f"banned cliché {w!r} present"))
    return findings


def is_compliant(text: str, spec: dict[str, Any] | None = None) -> bool:
    return not check_draft(text, spec)


__all__ = [
    "PROSE_STATE_FIELDS", "STRUCTURE_FINDING_TYPES", "StructureError",
    "validate_prose_state", "check_draft", "is_compliant",
]
