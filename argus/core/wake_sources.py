"""Normalize model wake hints into sources the Host can actually observe."""

from __future__ import annotations

import re
from collections.abc import Iterable

SUPPORTED_WAKE_SOURCES = (
    "authorization",
    "manager_stage",
    "artifact_revision",
    "subagent_terminal",
    "subagent_state",
)

WAKE_SOURCE_SYNONYMS = {
    "operator_answer": "authorization",
    "operator_message": "authorization",
    "artifact_change": "artifact_revision",
    "project_state": "manager_stage",
    "stage_change": "manager_stage",
}


def normalize_wake_sources(
    values: Iterable[object] | object,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Normalize case, synonyms, compounds, and duplicates in wake hints."""
    framed = (
        [values]
        if isinstance(values, str) or not isinstance(values, Iterable)
        else values
    )
    normalized: list[str] = []
    unknown: list[str] = []
    changed = False
    seen: set[str] = set()
    for value in framed:
        raw = str(value or "")
        tokens = [part.strip() for part in re.split(r"[|,]", raw) if part.strip()]
        if len(tokens) != 1 or (tokens and tokens[0] != raw):
            changed = True
        for token in tokens:
            folded = token.casefold()
            canonical = WAKE_SOURCE_SYNONYMS.get(folded, folded)
            if canonical != token:
                changed = True
            if canonical in seen:
                changed = True
                continue
            seen.add(canonical)
            if canonical in SUPPORTED_WAKE_SOURCES:
                normalized.append(canonical)
            else:
                unknown.append(canonical)
    return tuple(normalized), tuple(unknown), changed


__all__ = [
    "SUPPORTED_WAKE_SOURCES",
    "WAKE_SOURCE_SYNONYMS",
    "normalize_wake_sources",
]
