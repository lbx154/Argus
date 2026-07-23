"""Canonical research-direction vocabulary with legacy event compatibility."""
from __future__ import annotations

from typing import Any

RESEARCH_DIRECTIONS = frozenset({
    "continue",
    "redirect",
    "stop",
    "uncertain",
})

_LEGACY_ALIASES = {
    "go": "continue",
    "pivot": "redirect",
    "no_go": "stop",
    "no-go": "stop",
    "nogo": "stop",
    "undecided": "uncertain",
}


def normalize_research_direction(value: Any, *, default: str = "") -> str:
    """Return the canonical direction while accepting historical stored values."""
    raw = str(value or "").strip().lower()
    if raw in RESEARCH_DIRECTIONS:
        return raw
    return _LEGACY_ALIASES.get(raw, default)


__all__ = ["RESEARCH_DIRECTIONS", "normalize_research_direction"]
