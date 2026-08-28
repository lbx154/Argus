"""fiction_writing revise adapter: consume a shared literary review -> plan.

The fiction end of the Review-Contract closed loop. The reviewer's structured
output (``fiction/review.json`` today) is parsed and validated by the SHARED
contract (:mod:`argus_skill.verticals.literary.shared.review_contract`) against
fiction's own
finding VOCABULARY, then turned into an ordered revision plan the revise stage
acts on — blocking continuity findings first, each carrying the
``must_not_break`` invariants the revision must not violate while fixing.

No new reviewer agent: the framework Reviewer role produces the review; this is
only the consumer that binds its findings to fiction's revise step.
"""
from __future__ import annotations

from typing import Any

from ..literary.shared.review_contract import extract_review, normalize_review, revision_plan

#: Blocking continuity finding types (mirror the reviewer skill + the engine's
#: machine-decidable guarantees). A finding typed outside the fiction vocabulary
#: is rejected by the shared contract. ``temporal_consistency`` is arithmetic-
#: provable over story_state (age == current_year − birth_year; no birth in the
#: future; timeline order vs year) — see :mod:`.temporal`. ``verbatim_copy`` is
#: likewise machine-provable (a long shared verbatim run is a fact, not a taste),
#: the '不能抄' hard line — see :mod:`.novelty`.
FICTION_CONTINUITY_TYPES: frozenset[str] = frozenset({
    "status", "knowledge", "item_location", "co_location", "timeline",
    "world_rule", "motivation", "foreshadowing", "viewpoint", "language",
    "temporal_consistency", "verbatim_copy",
})

#: Non-blocking craft / AI-tell finding types (heuristic observations). ``voice``
#: may still block when an explicit forbidden_lexicon constraint is violated; word-list
#: AI-tell hits remain advisory.
FICTION_CRAFT_TYPES: frozenset[str] = frozenset({
    "style", "voice", "concreteness", "show_tell", "over_summary",
    "mechanical_twist", "pacing", "ending", "ai_tell",
})

FICTION_FINDING_TYPES: frozenset[str] = FICTION_CONTINUITY_TYPES | FICTION_CRAFT_TYPES


def fiction_revision_plan(review_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a review dict against fiction's vocabulary and return the plan."""
    review = normalize_review(review_raw, type_vocabulary=FICTION_FINDING_TYPES)
    return revision_plan(review)


def fiction_revision_plan_from_text(text: str) -> list[dict[str, Any]]:
    """Parse raw reviewer OUTPUT text, validate, and return the plan.

    Malformed / missing JSON raises (never silently treated as 'nothing to fix').
    """
    review = extract_review(text, type_vocabulary=FICTION_FINDING_TYPES)
    return revision_plan(review)


__all__ = [
    "FICTION_CONTINUITY_TYPES",
    "FICTION_CRAFT_TYPES",
    "FICTION_FINDING_TYPES",
    "fiction_revision_plan",
    "fiction_revision_plan_from_text",
]
