"""Detect semantically repeated Reviewer blockers across Engineer rounds."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..core.models import ReviewDecision

_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:/[\w.@+~:-]+){2,}")
_TIME_RE = re.compile(r"\b\d{2,4}[-/:T]\d{1,2}(?:[-/:T]\d{1,2})?(?:[.Z+:-]\d+)*\b")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_.-]{2,}")
_STOPWORDS = frozenset({
    "after", "again", "before", "current", "from", "into", "must",
    "next", "only", "remain", "remains", "rerun", "round", "same",
    "should", "stage", "that", "then", "this", "using", "with",
})


@dataclass(frozen=True)
class FailureSignature:
    digest: str
    terms: frozenset[str]
    failure_cause: str
    unsatisfied_items: tuple[str, ...]


def _normalise_terms(text: str) -> set[str]:
    value = _PATH_RE.sub(" path ", str(text or "").casefold())
    value = _TIME_RE.sub(" time ", value)
    value = _NUMBER_RE.sub(" number ", value)
    return {
        token for token in _TOKEN_RE.findall(value)
        if token not in _STOPWORDS and len(token) >= 4
    }


def review_failure_signature(review: ReviewDecision) -> FailureSignature | None:
    if review.status != "continue":
        return None
    report = review.planner_report if isinstance(review.planner_report, dict) else {}
    if report.get("plan_signal") == "reconsider":
        return None
    blocker = str(report.get("blocker") or "")
    recommended = str(report.get("recommended_next") or review.next_action or "")
    unsatisfied = tuple(sorted(
        str(item.get("item") or "").strip()
        for item in (review.checklist or [])
        if isinstance(item, dict)
        and not bool(item.get("satisfied"))
        and str(item.get("item") or "").strip()
    ))
    failure_cause = str(review.failure_cause or "").strip().casefold()
    if not failure_cause and not unsatisfied and not blocker.strip():
        return None
    terms = _normalise_terms(" ".join((failure_cause, blocker, recommended, *unsatisfied)))
    if not failure_cause and not unsatisfied and len(terms) < 3:
        return None
    canonical = "|".join([
        failure_cause,
        ",".join(unsatisfied),
        ",".join(sorted(terms)),
    ])
    return FailureSignature(
        digest=hashlib.sha256(canonical.encode()).hexdigest()[:16],
        terms=frozenset(terms),
        failure_cause=failure_cause,
        unsatisfied_items=unsatisfied,
    )


def signature_similarity(left: FailureSignature, right: FailureSignature) -> float:
    union = left.terms | right.terms
    lexical = len(left.terms & right.terms) / len(union) if union else 0.0
    same_cause = bool(left.failure_cause and left.failure_cause == right.failure_cause)
    same_items = bool(
        left.unsatisfied_items and left.unsatisfied_items == right.unsatisfied_items
    )
    if same_items and lexical >= 0.2:
        return max(lexical, 0.9)
    if same_cause and lexical >= 0.25:
        return max(lexical, 0.65)
    return lexical


__all__ = ["FailureSignature", "review_failure_signature", "signature_similarity"]
