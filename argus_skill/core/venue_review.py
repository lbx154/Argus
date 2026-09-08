"""Explicit Reviewer recommendations for the final paper acceptance boundary.

The Reviewer judges the science. The host enforces the operator's minimum
recommendation and binds that judgment to the selected venue and rendered work.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

RECOMMENDATIONS = (
    "strong_reject", "reject", "weak_reject", "borderline",
    "weak_accept", "accept", "strong_accept", "best_paper",
)
ACCEPTED_RECOMMENDATIONS = frozenset({"weak_accept", "accept", "strong_accept", "best_paper"})
FINAL_REVIEW_FEEDBACK_CHARS = 12_000


def requires_venue_review(*, vertical: str, stage: str, scope: str = "", operation: str = "evaluate") -> bool:
    return (
        str(vertical).strip().lower() == "research"
        and operation == "evaluate"
        and (str(stage).strip().lower() == "review" or str(scope).strip().lower().replace("-", "_") == "final_submission")
    )


def selected_venue(state_root: Path | str) -> str:
    from .pipeline_state import read_pipeline_state

    state = read_pipeline_state(state_root)
    return str(state.get("target_venue") or state.get("venue") or "").strip()


def configure_venue_revisions(config: Any) -> None:
    """Remove quality-convergence ceilings while preserving operational stops."""
    config.max_rounds = 0
    config.stall_threshold = 0
    config.soft_round_limit = 0
    config.hard_escalate_rounds = 0
    config.decision_progress_timeout_seconds = 0
    config.require_independent_review = True


def _venue_key(value: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return re.sub(r"(?:20)?\d{2}$", "", compact)


def normalize_venue_review(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    venue = value.get("venue")
    rationale = value.get("rationale")
    recommendation = re.sub(r"[\s-]+", "_", str(value.get("recommendation") or "").strip().lower())
    if recommendation == "best_paper_level":
        recommendation = "best_paper"
    issues = value.get("blocking_issues")
    if (
        not isinstance(venue, str) or not venue.strip()
        or not isinstance(rationale, str) or not rationale.strip()
        or recommendation not in RECOMMENDATIONS
        or not isinstance(value.get("acceptance_clear"), bool)
        or not isinstance(issues, list)
        or any(not isinstance(issue, str) or not issue.strip() for issue in issues)
    ):
        return None
    # Identity/binding fields emitted by the model are deliberately discarded.
    normalized: dict[str, Any] = {
        "venue": venue.strip(), "recommendation": recommendation,
        "acceptance_clear": value["acceptance_clear"], "rationale": rationale.strip(),
        "blocking_issues": [issue.strip() for issue in issues],
    }
    if "revision_required" in value:
        if not isinstance(value["revision_required"], bool):
            return None
        normalized["revision_required"] = value["revision_required"]
    return normalized


def venue_review_issue(value: Any, *, venue: str) -> str:
    assessment = normalize_venue_review(value)
    if not venue:
        return "no selected venue for the final paper review"
    if assessment is None:
        return "missing or invalid explicit venue recommendation"
    if _venue_key(assessment["venue"]) != _venue_key(venue):
        return f"reviewed venue {assessment['venue']!r} differs from selected venue {venue!r}"
    if assessment["recommendation"] not in ACCEPTED_RECOMMENDATIONS:
        return f"venue recommendation is {assessment['recommendation']}; weak_accept or better is required"
    if assessment["acceptance_clear"] is not True:
        return "Reviewer did not clearly support acceptance at the selected venue"
    if assessment["blocking_issues"]:
        return "reject-level issues remain: " + "; ".join(assessment["blocking_issues"])
    if assessment.get("revision_required") is True:
        return "the final Reviewer has actionable high-impact improvements awaiting Engineer revision and re-review"
    return ""


def paper_review_snapshot(project_root: Path | str) -> dict[str, str] | None:
    """Bind the actual paper closure, including rendered PDF and figure bytes."""
    from .manuscript_narrative_runtime import manuscript_closure_sha256

    root = Path(project_root)
    if not (root / "paper/main.tex").is_file() or not (root / "paper/main.pdf").is_file():
        return None
    try:
        return {"sha256": manuscript_closure_sha256(root)}
    except (OSError, ValueError):
        return None


def current_venue_acceptance_issue(review: Any, *, state_root: Path | str, artifact_root: Path | str) -> str:
    """Finalizers use the same recommendation and exact-file binding as Reviewer."""
    issue = venue_review_issue(getattr(review, "venue_review", None), venue=selected_venue(state_root))
    if issue:
        return issue
    if getattr(review, "review_source", "") != "reviewer":
        return "final venue recommendation was not supplied by the independent Reviewer"
    recorded = getattr(review, "venue_review_snapshot", None)
    current = paper_review_snapshot(artifact_root)
    if not recorded or not current:
        return "venue recommendation is not bound to a complete source and rendered paper"
    if recorded != current:
        return "the paper or its figures changed after the venue recommendation"
    return ""


def enforce_venue_acceptance(
    decision: Any, *, venue: str, before: dict[str, str] | None, artifact_root: Path | str,
) -> None:
    """Apply the stated minimum to the Reviewer's own assessment, never re-grade it."""
    decision.venue_review_required = True
    decision.venue_review_passed = False
    decision.venue_review_snapshot = before
    assessment = normalize_venue_review(decision.venue_review)
    if assessment is None or (venue and _venue_key(assessment["venue"]) != _venue_key(venue)):
        # An incomplete/wrong-venue response is a Reviewer failure. Retry that
        # read-only leg; do not ask Engineer to alter a paper to fix the protocol.
        decision.status = "blocked"
        decision.backend_unavailable = True
        decision.backend_stop_kind = "backend_unavailable"
        decision.reason = "Final Reviewer omitted a valid assessment for the selected venue."
        decision.next_action = "Retry the independent Reviewer on the same paper and clarify its actual recommendation for the selected venue in ordinary prose."
        return
    current = paper_review_snapshot(artifact_root)
    issue = venue_review_issue(assessment, venue=venue)
    if not before or not current:
        issue = "a complete manuscript source and rendered PDF are required for final acceptance"
    elif before != current:
        issue = "the paper or its figures changed during review; a current venue recommendation is required"
    decision.venue_review = assessment
    if not issue:
        decision.venue_review_passed = True
        return
    if decision.status == "done" or (decision.status == "blocked" and not decision.operator_question and not decision.backend_unavailable):
        decision.status = "continue"
    decision.reason = f"Final venue acceptance is pending: {issue}.\n\n{decision.reason}"
    if not decision.next_action.strip():
        repairs = "; ".join(assessment["blocking_issues"]) or assessment["rationale"]
        decision.next_action = "Revise the current paper against the selected venue's standard: " + repairs


def venue_review_instruction(venue: str) -> str:
    return (
        "## Final paper acceptance — mandatory operator standard\n"
        f"Act as an independent reviewer for the currently selected venue: {venue or '(not selected)'}. "
        "Read its researched criteria and the actual current manuscript, rendered pages, "
        "and claim-critical evidence. Judge novelty, significance, soundness, evidence, "
        "reproducibility, presentation, and fit at that venue. Finishing edits, compiling, "
        "an old certificate, or the Engineer's confidence is not an acceptance decision.\n"
        "Only a clear weak accept, accept, strong accept, or best-paper-level recommendation "
        "with no reject-level issues permits completion. Borderline, rejection, "
        "uncertain acceptance, or incomplete evidence requires further revision and concrete "
        "repairs. Do not inflate a rating to end the loop or treat the minimum as a target "
        "answer. Ordinary paper weaknesses must be revised, not escalated to the operator "
        "as a request to lower the bar. Keep the selected venue fixed. There is no quality "
        "revision-count ceiling; explicit operator stops and resource/transport failures "
        "remain distinct from acceptance.\n"
        "When the weakness is scientific, request the decisive method, baseline, control, "
        "or evidence repair in the current stage, rather than polishing unsupported claims. "
        "Use the existing plan-challenge channel when a different technical plan is needed. "
        "A strong negative or boundary result can be publishable, but completeness and "
        "honesty alone do not establish novelty or significance.\n"
        "Give constructive, creative, respectful feedback that helps Engineer aim for "
        "strong acceptance and best-paper quality. Begin with specific evidence-backed "
        "strengths and verified progress since the preceding review; close resolved issues "
        "instead of repeating them. Explain what is promising and why it is worth building on. "
        "Use encouragement tied to real work, without generic praise, personal criticism, "
        "invented strengths, or rating inflation.\n"
        "Prioritize the most valuable feasible improvements. For each, identify the precise "
        "scientific or presentation opportunity, a concrete change, and a decisive validation "
        "with controls and a success criterion. Suggest novel hypotheses, method alternatives, "
        "or explanatory analyses when useful; label untested ideas as hypotheses and propose "
        "the cheapest informative test before costly expansion. A failed test is evidence, "
        "not permission to hide the result or pretend the idea worked.\n"
        "The final review is a revision loop: explain the outstanding actionable high-impact "
        "work, even when the current rating is already weak accept or higher. Ask Engineer to "
        "continue revising while such work remains; Engineer must "
        "address it and return the current paper for your independent re-review. Check the "
        "actual changes and validation, not a promise to revise. Credit resolved work "
        "and do not reopen it without new evidence. Distinguish "
        "acceptance blockers from these feasible quality improvements and from speculative "
        "future-work ideas that do not hold completion. Completion is justified "
        "when no remaining actionable high-impact repair is supported; do not manufacture "
        "endless optional experiments. Clear weak accept remains the minimum, while "
        "best-paper quality is the aspiration.\n"
        "Write the review naturally in the operator's language. No JSON, fixed fields, "
        "decision footer, or prescribed review template is required. State your actual "
        "current recommendation for the selected venue, its evidence, and whether Engineer "
        "should make further changes before completion in your own words. A best-paper-level "
        "assessment describes your judgment of quality, not an award. The host records "
        "the actual reviewed manuscript version.\n"
    )
