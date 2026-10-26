"""Explicit Reviewer recommendations for the final paper acceptance boundary.

The Reviewer judges the science. The host enforces the operator's minimum
recommendation and binds that judgment to the selected venue and rendered work.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping

RECOMMENDATIONS = (
    "strong_reject", "reject", "weak_reject", "borderline",
    "weak_accept", "accept", "strong_accept", "best_paper",
)
ACCEPTED_RECOMMENDATIONS = frozenset({"weak_accept", "accept", "strong_accept", "best_paper"})
#: The intervention that keeps a finished verdict open below the operator's bar.
ACCEPTANCE_BAR_POLICY = "operator_acceptance_bar"


def requires_venue_review(
    *, vertical: str, stage: str, scope: str = "", operation: str = "evaluate",
    artifact_root: Path | str | None = None,
) -> bool:
    """Whether a review is the paper's final venue review.

    The paper's review stage always is. A final acceptance outside it is one
    only for a submission-shaped deliverable: given ``artifact_root``, a
    workspace with no paper (a survey, a report) is accepted by its ordinary
    review, since no venue recommendation could ever bind to it.
    """
    if str(vertical).strip().lower() != "research" or operation != "evaluate":
        return False
    if str(stage).strip().lower() == "review":
        return True
    return str(scope).strip().lower().replace("-", "_") == "final_submission" and (
        artifact_root is None or has_submission_candidate(artifact_root)
    )


def has_submission_candidate(artifact_root: Path | str) -> bool:
    """Whether the workspace holds a paper (its source or its rendered PDF)."""
    root = Path(artifact_root)
    return any((root / relative).is_file() for relative in ("paper/main.tex", "paper/main.pdf"))


def selected_venue(state_root: Path | str) -> str:
    from .pipeline_state import read_pipeline_state

    state = read_pipeline_state(state_root)
    return str(state.get("target_venue") or state.get("venue") or "").strip()


# Shown where a venue name would be when none was selected. A project can reach
# its final review with nothing having set ``target_venue``; the Reviewer then
# judges against the stated standard and the operator's bar, and its verdict
# counts. The recorded ``venue`` stays empty rather than holding this label.
UNSELECTED_VENUE = "the stated standard (no venue selected)"


def _venue_phrase(venue: str) -> str:
    return "the selected venue" if venue else "the stated standard"


def selected_acceptance_minimum(state_root: Path | str) -> str:
    """Read the operator's bar, independently of the Reviewer's own rating."""
    from .pipeline_state import read_pipeline_state

    value = read_pipeline_state(state_root).get("venue_acceptance_minimum", "weak_accept")
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


def _venue_key(value: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return re.sub(r"(?:20)?\d{2}$", "", compact)


def normalize_venue_review(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    venue = value.get("venue", "")
    rationale = value.get("rationale")
    recommendation = re.sub(r"[\s-]+", "_", str(value.get("recommendation") or "").strip().lower())
    if recommendation == "best_paper_level":
        recommendation = "best_paper"
    issues = value.get("blocking_issues")
    if (
        not isinstance(venue, str)
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


def venue_review_issue(value: Any, *, venue: str, minimum: str = "weak_accept") -> str:
    assessment = normalize_venue_review(value)
    if assessment is None:
        return "missing or invalid explicit venue recommendation"
    # With no selected venue there is nothing to bind the verdict to; with one,
    # the verdict must be for that venue.
    if venue and _venue_key(assessment["venue"]) != _venue_key(venue):
        return f"reviewed venue {assessment['venue']!r} differs from selected venue {venue!r}"
    if minimum not in ACCEPTED_RECOMMENDATIONS:
        return f"invalid operator venue acceptance minimum: {minimum!r}"
    if RECOMMENDATIONS.index(assessment["recommendation"]) < RECOMMENDATIONS.index(minimum):
        return f"venue recommendation is {assessment['recommendation']}; {minimum} or better is required"
    if assessment["acceptance_clear"] is not True:
        return f"Reviewer did not clearly support acceptance at {_venue_phrase(venue)}"
    if assessment["blocking_issues"]:
        return "reject-level issues remain: " + "; ".join(assessment["blocking_issues"])
    if assessment.get("revision_required") is True:
        return "the final Reviewer has actionable high-impact improvements awaiting Engineer revision and re-review"
    return ""


def paper_review_snapshot(project_root: Path | str) -> dict[str, Any] | None:
    """Bind the actual paper closure, including rendered PDF and figure files."""
    from .manuscript_narrative_runtime import manuscript_closure_facts

    root = Path(project_root)
    if not (root / "paper/main.tex").is_file() or not (root / "paper/main.pdf").is_file():
        return None
    try:
        return {"files": manuscript_closure_facts(root)}
    except (OSError, ValueError):
        return None


def current_venue_acceptance_issue(review: Any, *, state_root: Path | str, artifact_root: Path | str) -> str:
    """Finalizers use the same recommendation and exact-file binding as Reviewer."""
    issue = venue_review_issue(
        getattr(review, "venue_review", None), venue=selected_venue(state_root),
        minimum=selected_acceptance_minimum(state_root),
    )
    if issue:
        return issue
    if getattr(review, "review_source", "") != "reviewer":
        return "final venue recommendation was not supplied by the independent Reviewer"
    recorded = getattr(review, "venue_review_snapshot", None)
    current = paper_review_snapshot(artifact_root)
    # A binding from before file facts named a digest that can no longer be checked.
    if not isinstance(recorded, dict) or not recorded.get("files") or not current:
        return "venue recommendation is not bound to a complete source and rendered paper"
    if recorded != current:
        return "the paper or its figures changed after the venue recommendation"
    return ""


def enforce_venue_acceptance(
    decision: Any, *, venue: str, before: dict[str, Any] | None, artifact_root: Path | str,
    minimum: str = "weak_accept",
) -> None:
    """Hold the operator's minimum against the Reviewer's own assessment.

    The verdict stays as the Reviewer wrote it. A finished verdict whose
    assessment misses the operator's bar keeps ``venue_review_passed`` False,
    which keeps the mission open (``ReviewDecision.final_submission_certified``),
    and the host records why beside the verdict.
    """
    decision.venue_review_required = True
    decision.venue_review_passed = False
    decision.venue_review_snapshot = before
    assessment = normalize_venue_review(decision.venue_review)
    if decision.venue_review is None and decision.status != "done":
        return
    if assessment is None or (venue and _venue_key(assessment["venue"]) != _venue_key(venue)):
        # An incomplete/wrong-venue response is a Reviewer failure. Retry that
        # read-only leg; do not ask Engineer to alter a paper to fix the protocol.
        # The retried verdict is no longer what the Reviewer judged, so its
        # learning judgment goes and reflection runs.
        decision.status = "manager"
        decision.learning = decision.learning_reason = ""
        decision.backend_unavailable = True
        decision.backend_stop_kind = "backend_unavailable"
        decision.reason = f"Final Reviewer omitted a valid assessment for {_venue_phrase(venue)}."
        decision.next_action = (
            "Retry the independent Reviewer on the same paper and submit a review action "
            f"with its actual recommendation for {_venue_phrase(venue)}."
        )
        return
    current = paper_review_snapshot(artifact_root)
    issue = venue_review_issue(assessment, venue=venue, minimum=minimum)
    if not before or not current:
        issue = "a complete manuscript source and rendered PDF are required for final acceptance"
    elif before != current:
        issue = "the paper or its figures changed during review; a current venue recommendation is required"
    decision.venue_review = assessment
    if not issue:
        decision.venue_review_passed = True
    elif decision.status == "done":
        decision.interventions = [*decision.interventions, {
            "policy": ACCEPTANCE_BAR_POLICY,
            "action": "kept_open",
            "at": time.time(),
            "note": (
                f"the operator's bar ({minimum.replace('_', ' ')} or better) is not met, so "
                f"the paper stays open: {issue}. Revise the current paper against "
                + ("the selected venue's standard" if venue else "the stated standard")
                + " and return it for independent review"
            ),
        }]


def venue_review_instruction(venue: str, *, minimum: str = "weak_accept") -> str:
    minimum_label = minimum.replace("_", " ")
    return (
        "## Final paper acceptance — mandatory operator standard\n"
        + (
            f"Act as an independent reviewer for the currently selected venue: {venue}. "
            "Read its researched criteria and the actual current manuscript, rendered pages, "
            if venue else
            "No venue has been selected for this paper. Act as an independent reviewer for a "
            "strong venue in its field, say which standard you applied, and read the actual "
            "current manuscript, rendered pages, "
        ) +
        "and claim-critical evidence. Judge novelty, significance, soundness, evidence, "
        "reproducibility, presentation, and fit at that venue. Finishing edits, compiling, "
        "an old certificate, or the Engineer's confidence is not an acceptance decision.\n"
        f"The operator's completion bar is {minimum_label}. Only a clear {minimum_label} "
        "or stronger recommendation "
        "with no reject-level issues permits completion. Borderline, rejection, "
        "uncertain acceptance, or incomplete evidence requires further revision and concrete "
        "repairs. Do not inflate a rating to end the loop or treat the minimum as a target "
        "answer. Ordinary paper weaknesses must be revised, not escalated to the operator "
        "as a request to lower the bar. Keep the selected venue fixed. There is no quality "
        "revision-count ceiling; explicit operator stops and resource/transport failures "
        "remain distinct from acceptance.\n"
        "When the weakness is scientific, request the decisive method, baseline, control, "
        "or evidence repair in the current stage, rather than polishing unsupported claims. "
        "This is full conference peer review, including contribution, novelty, methodology, "
        "experimental design, results, and conclusions, not only a formatting pass. "
        "Engineer directly implements the requested method, baseline, control, or experiment "
        "changes inside this same final Review mission, then updates the paper and returns "
        "for independent peer review. Keep the stage at Review; do not request rollback, "
        "reopen selection, or send ordinary scientific revisions back through earlier stages. "
        "A strong negative or boundary result can be publishable, but completeness and "
        "honesty alone do not establish novelty or significance.\n"
        "Judge the current version honestly even when it falls below the operator's bar. "
        "A higher bar is a research objective, not a requested rating. Explain the remaining "
        "contribution or evidence gap and a promising, feasible way to address it. For a "
        "strong-accept target, a useful mechanism, effective method, broader predictive "
        "principle, or decisive external test can matter more than another compliance check. "
        "Try the cheapest informative version first; keep adverse results and reassess "
        "the proposed direction instead of endlessly repeating an uninformative experiment.\n"
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
        "Help strengthen the work, not merely make its claims more conservative. Prefer "
        "constructive method improvements, stronger comparisons, new explanatory evidence, "
        "and experiments that can establish a more substantial contribution. Do not make "
        "deleting claims or adding caveats the default remedy when a feasible scientific "
        "repair can address the concern. Revise a claim when actual evidence requires it.\n"
        "Keep most revision effort on the science. Check that the paper has at least two "
        "informative figures, usually three, supporting its mechanism and main evidence. "
        "Figure design can proceed in isolated parallel subtasks while Engineer works on "
        "methods and experiments. Once a composition is accurate, readable and refined, "
        "keep it fixed unless new evidence, changed science or a concrete readability "
        "defect requires an update. Do not reopen accepted figures for stylistic preference "
        "or let optional cosmetic changes delay scientific review.\n"
        "The final review is a revision loop: explain the outstanding actionable high-impact "
        "work, even when the current rating is already weak accept or higher. Ask Engineer to "
        "continue revising while such work remains; Engineer must "
        "address it and return the current paper for your independent re-review. Check the "
        "actual changes and validation, not a promise to revise. Credit resolved work "
        "and do not reopen it without new evidence. Distinguish "
        "acceptance blockers from these feasible quality improvements and from speculative "
        "future-work ideas that do not hold completion. Completion is justified "
        "when no remaining actionable high-impact repair is supported; do not manufacture "
        f"endless optional experiments. Clear {minimum_label} remains the minimum, while "
        "best-paper quality is the aspiration.\n"
        "Write the review naturally in the operator's language. No JSON, fixed fields, "
        "decision footer, or prescribed review template is required. State your actual "
        "current recommendation for the selected venue, its evidence, and whether Engineer "
        "should make further changes before completion in your own words. A best-paper-level "
        "assessment describes your judgment of quality, not an award. The host records "
        "the actual reviewed manuscript version. Your review becomes the current "
        "paper/REVIEW.md: give the whole current report each round with current strengths, "
        "resolved concerns, remaining opportunities and useful next experiments, rather "
        "than repeating an obsolete judgment.\n"
    )
