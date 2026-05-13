"""Compatibility wrapper for the legacy mission reviewer API."""
from __future__ import annotations

from ..engineer.reviewer import Reviewer, ReviewerConfig

MissionReviewer = Reviewer
MissionReviewerConfig = ReviewerConfig

__all__ = ["MissionReviewer", "MissionReviewerConfig"]
