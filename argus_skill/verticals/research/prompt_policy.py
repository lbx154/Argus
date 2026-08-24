"""Research-owned dynamic Planner and Reviewer prompt fragments."""

from __future__ import annotations

from pathlib import Path


def academic_paper_review_block() -> str:
    return (
        "## Near-complete paper review\n"
        "Be a skeptical program-committee reviewer: require a clear contribution, "
        "credible comparisons, sufficient evidence/statistics, accurate citations, "
        "readable writing, and clean figures/layout. `done` requires the applicable "
        "final checklist with no critical blocker; do not reward polish without "
        "substantive evidence. Rebuild the manuscript and inspect the generated "
        "artifact: reject undefined citations, bibliography warnings, significant "
        "overfull boxes or clipped pages, and missing PDF title/author metadata. "
        "Render the relevant pages when layout matters."
    )


def _parallel_drafting_block(stage: str, project_root: Path | None) -> str:
    if stage not in {"run", "analysis"}:
        return ""
    from ...skills.stage_machine import format_stage_checklist

    draft_checklist = format_stage_checklist(
        "draft",
        role="planner",
        project_root=project_root,
    )
    caveat = (
        "At `analysis`, keep every touched claim/evidence artifact internally "
        "consistent or explicitly placeholder-only."
        if stage == "analysis"
        else "At `run`, prose is unblocked but final outcomes remain unknown."
    )
    return (
        "## Parallel paper-drafting track (run/analysis only)\n"
        f"`current_stage` is `{stage}`. When a long experiment is already running "
        "under its own supervision, delegate one bounded drafting task instead of "
        "spending a round only waiting. It may extend Introduction, Related Work, "
        "Background, Problem Definition, Method, Experimental Setup, or Results "
        "scaffolding.\n\n"
        "Do not advance or edit `.argus/PIPELINE_STATE.json`. Never invent a final "
        "metric, comparison, significance test, or outcome-dependent claim: use an "
        "explicit `TBD`/`PLACEHOLDER` and record its source artifact and backfill "
        "condition in `paper/RESULT_PLACEHOLDERS.md`. Keep one lightweight health "
        "check on the live run, and judge this pass by useful prose plus placeholder "
        f"integrity. {caveat}\n\n"
        "Draft-stage checklist for shaping scope only; do not mark it complete:\n"
        f"{draft_checklist}"
    )


def _planner_upstream_block(stage: str) -> str:
    from .stages import CANONICAL_STAGE_ORDER

    try:
        stage_index = CANONICAL_STAGE_ORDER.index(stage)
    except ValueError:
        stage_index = 0
    earlier = ", ".join(CANONICAL_STAGE_ORDER[:stage_index]) or "(none)"
    return (
        "## Upstream research defect handling\n"
        f"Current stage: `{stage or '(unknown)'}`. Earlier stages: {earlier}.\n"
        "If an earlier paper artifact is missing, stale, or unreliable, inspect the "
        "expected artifact, its checklist, pipeline state, and nearby evidence before "
        "calling it broken. Report the earliest broken stage and concrete evidence; "
        "the Manager owns rollback. Do not edit `.argus/PIPELINE_STATE.json`, and do "
        "not continue work whose claims depend on the broken evidence."
    )


def _planner_fragment(stage: str, project_root: Path | None) -> str:
    blocks = [
        _parallel_drafting_block(stage, project_root),
        _planner_upstream_block(stage),
        _planner_manuscript_block(project_root),
        (
            "## Research paper infrastructure\n"
            "Trust a fresh model-backed `paper/PAPER_INFRASTRUCTURE_REVIEW.json`. "
            "If it is missing or stale, run its generator instead of substituting an "
            "ad hoc keyword scan."
        ),
    ]
    return "\n\n".join(block for block in blocks if block)


def _manuscript_exists(project_root: Path | None) -> bool:
    """Is there something here that a reviewer would call a manuscript?

    Deliberately crude -- a file with a document body in it. Anything finer
    would be the host deciding what counts as a paper, which is the judgement
    this gate exists to hand to the Reviewer.
    """
    if project_root is None:
        return False
    try:
        tex = (Path(project_root) / "paper" / "main.tex").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return False
    return "\\begin{document}" in tex


def _planner_manuscript_block(project_root: Path | None) -> str:
    """Put the paper in front of the role that decides what gets worked on.

    Only draft, review and submission carry paper-facing checklist items, and
    five of seven campaigns write their manuscript from stages that carry none.
    The Planner creates the missions, so with no paper question it plans the one
    figure it has a word for: every figure mission run-06-control ever queued
    was about Figure 1 -- build it, make it carry the argument, ask whether it
    is real -- while three finished result figures sat unused in paper/figures
    beside a two-figure manuscript.

    The question stays a question. Which claims the paper asks a reader to take
    on trust cannot be answered from here; naming faults would replace the
    reading rather than provoke it.
    """
    if not _manuscript_exists(project_root):
        return ""
    return (
        "## The paper is work\n"
        "A manuscript exists, and its gaps are missions like any other rather "
        "than something that happens at the end. Read it: ask which claims a "
        "reader has to take on trust because no figure shows them, where the "
        "evidence is thinner than in the accepted papers this campaign chose, "
        "and what a reviewer would reject it for. Queue that work now. A figure "
        "programme is one mission per claim that needs showing, not another "
        "pass over Figure 1."
    )


def _reviewer_fragment(stage: str, scope: str, project_root: Path | None) -> str:
    blocks: list[str] = []
    # Reading the paper as a paper used to wait for the campaign to declare a
    # writing stage. They do not: run-07 held a twenty-page manuscript at
    # `benchmark` and spent a hundred and seventy consecutive reviews checking
    # whether a measurement packet had the right JSON files in it, never once
    # noticing it had no figures. run-04, at `review`, read main.tex end to
    # end, pulled the PDF text and looked at the rendered pages -- same
    # Reviewer, same model, one condition apart.
    #
    # So the question is asked as soon as there is something to ask it about.
    # This routes authority; it does not spend it. What is wrong with the
    # manuscript stays the Reviewer's to find by reading, which is the only
    # way faults nobody enumerated in advance are ever found.
    if (
        scope == "final_submission"
        or stage in {"review", "submission"}
        or _manuscript_exists(project_root)
    ):
        blocks.append(academic_paper_review_block())
    if scope == "final_submission":
        blocks.append(
            "## Final paper review\n"
            "Read the current manuscript, rendered PDF, and claim-critical sources "
            "as an independent venue reviewer. Use `done` only when the research "
            "objective and selected venue bar are genuinely met; otherwise return "
            "`continue` with the few highest-leverage scientific or writing changes. "
            "Do not require or manufacture an assurance memo, reviewer-question "
            "bundle, or other certification packet."
        )
    return "\n\n".join(blocks)


def _engineer_fragment(project_root: Path | None) -> str:
    """Name the figures this campaign has already drawn and not used.

    The Engineer is the role that writes the paper and puts figures in it, and
    it is the one role the altitude facts never reach. So the Reviewer asks for
    figures while the hand doing the work cannot see that run-06 had three
    result figures sitting in paper/figures with two of them in the paper, or
    that run-02 had thirteen files that were all the same overview redrawn.

    Files beside the paper are the one thing reading the paper cannot show.
    Everything else about the manuscript the Engineer can open and see, and
    listing that here would be the host doing its looking for it.
    """
    if project_root is None:
        return ""
    try:
        from .paper_structural_minimums import validate_paper_structural_minimums

        unused = [
            note.detail
            for note in validate_paper_structural_minimums(Path(project_root)).notes
            if note.code == "figures_drawn_but_unused"
        ]
    except Exception:  # noqa: BLE001 - context is advisory
        return ""
    if not unused:
        return ""
    return "## Already drawn\n" + unused[0] + "."


def render_role_prompt_fragment(
    *,
    role: str,
    operation: str,
    stage: str,
    scope: str,
    project_root: Path | None,
) -> str:
    """Render only policy owned by the Research paper vertical."""
    _ = operation
    normalized_role = str(role or "").strip().lower()
    normalized_stage = str(stage or "").strip().lower()
    normalized_scope = str(scope or "").strip().lower().replace("-", "_")
    if normalized_role == "planner":
        return _planner_fragment(normalized_stage, project_root)
    if normalized_role == "engineer":
        return _engineer_fragment(project_root)
    if normalized_role == "reviewer":
        return _reviewer_fragment(
            normalized_stage, normalized_scope, project_root
        )
    return ""


__all__ = ["academic_paper_review_block", "render_role_prompt_fragment"]
