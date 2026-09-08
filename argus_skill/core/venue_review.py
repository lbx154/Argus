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
    return {
        "venue": venue.strip(), "recommendation": recommendation,
        "acceptance_clear": value["acceptance_clear"], "rationale": rationale.strip(),
        "blocking_issues": [issue.strip() for issue in issues],
    }


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
        decision.next_action = "Retry the independent Reviewer on the same paper and provide the required VENUE_REVIEW assessment."
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
    choices = ", ".join(RECOMMENDATIONS)
    return (
        "## Final paper acceptance — mandatory operator standard\n"
        f"Act as an independent reviewer for the currently selected venue: {venue or '(not selected)'}. "
        "Read its researched criteria and the actual current manuscript, rendered pages, "
        "and claim-critical evidence. Judge novelty, significance, soundness, evidence, "
        "reproducibility, presentation, and fit at that venue. Finishing edits, compiling, "
        "an old certificate, or the Engineer's confidence is not an acceptance decision.\n"
        "Only a clear weak_accept, accept, strong_accept, or best_paper recommendation "
        "with no reject-level issues may accompany STATUS=done. Borderline, rejection, "
        "uncertain acceptance, or incomplete evidence requires STATUS=continue and concrete "
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
        "Include this named line in every integrated final-stage decision (including "
        "continue); fill it with your own judgment, not the example's verdict:\n"
        'VENUE_REVIEW={"venue":"' + venue.replace('"', '') + '",'
        '"recommendation":"borderline","acceptance_clear":false,'
        '"rationale":"Explain the venue-level recommendation with specific evidence.",'
        '"blocking_issues":["Concrete issue that currently prevents acceptance."]}\n'
        f"Allowed recommendation values: {choices}. Use an empty blocking_issues list only "
        "when none remain. best_paper denotes your internal assessment of the paper's "
        "quality, not an award. The host records the actual reviewed manuscript version.\n"
    )
