from __future__ import annotations

import pytest

from argus_skill.core.research_direction import normalize_research_direction


@pytest.mark.parametrize(
    ("stored", "canonical"),
    [
        ("continue", "continue"),
        ("redirect", "redirect"),
        ("stop", "stop"),
        ("uncertain", "uncertain"),
        ("go", "continue"),
        ("pivot", "redirect"),
        ("no_go", "stop"),
        ("undecided", "uncertain"),
    ],
)
def test_research_direction_normalizes_current_and_legacy_values(
    stored: str,
    canonical: str,
) -> None:
    assert normalize_research_direction(stored) == canonical


def test_unknown_research_direction_uses_explicit_default() -> None:
    assert normalize_research_direction("unknown") == ""
    assert normalize_research_direction("unknown", default="uncertain") == "uncertain"
