"""End-to-end venue path: the final Review's language guidance.

Venue-specific format checks remain in the Paper skills while scientific,
visual, and language acceptance share the final Review stage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_venue_language_reviews_describe_claims_without_sentence_templates() -> None:
    root = Path(__file__).parents[1] / "argus/verticals/research/skills/reviewer"
    text = (root / "venue-academic-language-review.md").read_text(encoding="utf-8")
    assert "what is studied, what is claimed, under which conditions" in text
    assert "X is better for Y in Z because W" not in text
    assert "We propose X. We show X improves Y by Z because W" not in text
