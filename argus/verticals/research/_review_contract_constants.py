"""Shared lightweight helpers and constants for skill-generated review artifacts."""
from __future__ import annotations

from pathlib import Path

ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY = "argus.verticals.research.academic_language_review"
ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW_history.jsonl")
PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY = "argus.verticals.research.paper_infrastructure_review"
PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW_history.jsonl")
LAYOUT_REVIEW_GENERATED_BY = "argus.verticals.research.paper_layout_review"
LAYOUT_REVIEW_HISTORY_PATH = Path("paper/LAYOUT_REVIEW_history.jsonl")
