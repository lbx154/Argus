"""Research vertical stage definitions and active role policy.

Research moves through four stages, always forward:
``idea -> experiment -> paper -> review``. Experiment covers both building
the method and running the experiments, so the design can be revised freely
while the evidence comes in.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ...core.vertical_contract import IterationAssessment
from ...skills.stage_machine import ChecklistItem
from . import library_preparation
from .prompt_policy import render_role_prompt_context, render_role_prompt_fragment
from .review_purchase import review_purchase_policy

log = logging.getLogger(__name__)

LIBRARY_PREPARER = library_preparation.prepare_skill_libraries

CANONICAL_STAGE_ORDER: tuple[str, ...] = (
    "idea",
    "experiment",
    "paper",
    "review",
)

# Aliases remain useful at parse boundaries, while durable migration is invoked
# explicitly at Manager/runtime entry points.
STAGE_ALIASES = {
    "research": "idea",
    "plan": "experiment",
    "benchmark": "experiment",
    "build": "experiment",
    "run": "experiment",
    "analysis": "experiment",
    "draft": "paper",
    "submission": "review",
}


def _checklist(*items: ChecklistItem) -> tuple[ChecklistItem, ...]:
    return tuple(items)


STAGE_CHECKLISTS: dict[str, tuple[ChecklistItem, ...]] = {
    "idea": _checklist(
        ChecklistItem(
            id="idea.portfolio",
            statement=(
                "For a new broad publishable or doctoral paper mission, complete exactly "
                "twelve source-only routes, twelve independent route reviews, and one "
                "selector. Candidate execution is forbidden during selection. Full "
                "working outputs stay under internal `.argus` team storage."
            ),
            evidence_hint="internal `.argus/teams/...` task outputs",
        ),
        ChecklistItem(
            id="idea.selection",
            statement=(
                "For a staged broad paper mission, the selector makes one resumable "
                "choice; the project-root research notes, `RESEARCH_NOTES.md`, carry "
                "the winner into Experiment, with a one-line reason for setting aside "
                "every other route. For a staged direction the operator has fixed, the "
                "research notes instead examine and position the supplied idea without "
                "inventing a selector or routes that were set aside. The notes replace "
                "the previous text rather than accumulating history. The selection "
                "itself completes this stage; missing or thin notes are written on the "
                "way out and are never a reason to hold. A direct Idea-only request "
                "returns its independently reviewed result without writing that file."
            ),
            evidence_hint="the selected idea in the project's stage record",
        ),
    ),
    "experiment": _checklist(
        ChecklistItem(
            id="experiment.implementation",
            statement=(
                "Implement the selected mechanism and real strong published baselines "
                "through real entry points. Do not rename a local heuristic after a paper. "
                "Choose models for task competence and claim scope. Prefer appropriate "
                "existing public or official benchmarks with their released tasks, "
                "splits, protocols, and real evaluators. Small custom benchmarks are "
                "allowed under the cost-aware benchmark policy in "
                "research-experiment-playbook.md: no API calls or only a small amount "
                "within the authorized budget, respecting project-specific restrictions. Keep "
                "explicit run configuration beside the code and verify the smallest "
                "faithful path before claim-bearing execution."
            ),
            evidence_hint="code, explicit run configuration, and direct smoke output",
        ),
        ChecklistItem(
            id="experiment.fidelity",
            statement=(
                "Trace the actual call path and confirm the method, baseline, controls, "
                "information boundary, and evaluator test the selected idea. Repair "
                "implementation or setup defects in place; do not reopen selection or "
                "move the work backward. A hypothesis-to-code mapping must name the "
                "executed quantities and path rather than merely matching labels."
            ),
            evidence_hint="implemented entry points and their direct test output",
        ),
        ChecklistItem(
            id="experiment.positive_control",
            statement=(
                "Run a positive control with a known recoverable signal through the same "
                "executed path before "
                "interpreting a null or negative result. If the known detectable case "
                "fails, diagnose the implementation, evaluator, truncation, scale, or "
                "information boundary instead of treating the hypothesis as tested."
            ),
            evidence_hint="direct positive-control command, configuration, and raw output",
        ),
        ChecklistItem(
            id="experiment.adaptive",
            statement=(
                "Run an adaptive programme: every executed run is reproducible from its "
                "code, explicit configuration, and raw output, while methods, baselines, "
                "benchmarks, controls, and next experiments may change in response to "
                "development evidence. Design and execution live in the same stage, so "
                "revise the experimental design in place as evidence arrives. No frozen "
                "global experiment plan is required. Choose independent units, sample "
                "counts, and repetitions for the coverage and precision the claim needs; "
                "repeat stochastic runs when seed variation could change the conclusion. "
                "Freeze the comparison before held-out confirmation."
            ),
            evidence_hint="executed commands/configuration and raw experimental outputs",
        ),
        ChecklistItem(
            id="experiment.paper_bar",
            statement=(
                "Advance to Paper when credible evidence supports a scientifically "
                "meaningful scoped claim. Claims of superiority require beating the "
                "strongest same-information baseline. Comparisons must include real strong "
                "published baselines rather than renamed local heuristics, use models and "
                "tasks appropriate to the claim, and pass a positive control through the "
                "real evaluator. Evidence scale is part of "
                "the bar: a claim about models in general rests on several families and "
                "sizes, a claim about a phenomenon on enough independent items that "
                "templates cannot explain it; a handful of development items or one model "
                "family supports only a correspondingly narrow claim. Otherwise improve the "
                "method or experiment in the current Experiment stage."
                " After Reviewer accepts the current experiment, Planner must apply "
                "the post-result scale assessment in research-experiment-playbook.md "
                "against the operator objective. If insufficient, expand the existing "
                "experiment under that benchmark policy before requesting Paper; "
                "a narrower claim does not substitute for that assessment."
            ),
            evidence_hint="claim-bearing comparisons, controls, and direct raw outputs",
        ),
        ChecklistItem(
            id="experiment.repair",
            statement=(
                "Treat method, experiment, evaluator, and evidence defects as repair work "
                "inside Experiment. Keep the selected idea and current stage; never request "
                "a rollback. When a matched ablation or the strongest same-information "
                "baseline beats the mechanism on fresh evidence, the repair is to re-derive "
                "the thesis from what the evidence establishes and confirm it on untouched "
                "data, not to open another variant of the same objective. Unfinished "
                "development is not a negative result; a negative or boundary thesis earns "
                "Paper only with complete evidence for it."
            ),
            evidence_hint="repaired work products and the next decisive comparison",
        ),
        ChecklistItem(
            id="experiment.notes",
            statement=(
                "When the bar for entering Paper is met, overwrite the project-root "
                "research notes, `RESEARCH_NOTES.md`, with the thesis, the comparisons "
                "that establish it, the strongest baseline, the essential losses or "
                "limits, the figures and data to use, and the minimum pointers a writer "
                "needs to reproduce the results. Sort the complete evidence into "
                "headline, mechanism, disambiguating-control, scope-changing, and "
                "completeness evidence, and say where each lives, including repeats. "
                "The evidence decides whether Experiment is done; missing or stale notes "
                "are written by the round that advances and are never a reason to hold "
                "a stage whose science is complete."
            ),
            evidence_hint="RESEARCH_NOTES.md, written when advancing",
        ),
    ),
    "paper": _checklist(
        ChecklistItem(
            id="paper.argument",
            statement=(
                "Produce a complete paper draft led by the contribution and strongest "
                "result. Include every claim-bearing experiment, intended figure and "
                "table, citation, and venue-required section. Select and arrange evidence "
                "by its headline, mechanism, disambiguating-control, scope-changing, or "
                "completeness role: keep complete matrices in Methods, tables, or the "
                "Appendix while prose interprets the comparisons that change the current "
                "inference. Do not organize it as an experiment chronology or ship a "
                "development shortfall as a finding; a negative or boundary thesis is a "
                "paper only when its evidence is as complete as a positive one would need."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="paper.presentation",
            statement=(
                "Write to the standard of a strong accepted paper at the selected venue, "
                "with no house quota for abstract length, number density, or caption "
                "format: the claim decides how long, how numerical, and how hedged each "
                "passage is. Headline evidence appears where it establishes the claim, "
                "a caption tells the reader what to see, and the same headline number may "
                "recur for a different section role; do not apply a mechanical repetition "
                "cap or recite the same full result matrix in every location."
            ),
            evidence_hint="paper/main.tex and its rendered figures and tables",
        ),
        ChecklistItem(
            id="paper.work_products",
            statement=(
                "The manuscript, bibliography, figures, included source files, and rendered "
                "output are present, mutually consistent, and compile under the selected "
                "venue's current official rules. Conceptual and method figures follow "
                "paper-framework-figure-studio.md: default Method D (image-API blueprint "
                "and editable PPT Master reconstruction), with Method B local vector "
                "drawing as the disclosed fallback. Labels and topology are grounded "
                "in the manuscript and executed code, with editable source and an "
                "included vector PDF export. Quantitative charts stay data-driven. "
                "Reuse a suitable existing figure; draw only when needed. Default PDF "
                "placement is after Introduction, preferably on page 2 or 3, subject to "
                "the author kit and actual Introduction length. "
                "Final scientific review, strict visual "
                "inspection, and academic-language polishing happen only in Review."
            ),
            evidence_hint="paper/main.tex, rendered output, bibliography, figures, and includes",
        ),
        ChecklistItem(
            id="paper.notes",
            statement=(
                "Keep the project-root research notes, `RESEARCH_NOTES.md`, as the single "
                "upstream context for Paper, rewritten rather than accumulated. Do not "
                "create parallel project-visible context files. Neither their presence "
                "nor their wording decides whether Paper is done; the manuscript does."
            ),
            evidence_hint="RESEARCH_NOTES.md as context only",
        ),
    ),
    "review": _checklist(
        ChecklistItem(
            id="review.parallel",
            statement=(
                "Before narrative editing, preserve an immutable source/PDF snapshot in "
                "internal mission state. After the fresh-context edit, the host obtains independent "
                "read-only passes in parallel: before/after scientific semantic-loss, strict "
                "rendered visual quality, and a cold read whose isolated input contains only "
                "the current rendered PDF. The host skips semantic loss for identical verified "
                "snapshots and reuses PDF-only assessments only for identical input and policy. "
                "Engineer and Reviewer must not spawn duplicate passes. Keep pass results "
                "internal; the integrated Reviewer "
                "records their adjudicated result only in `paper/REVIEW.md`. Until calibration "
                "promotes them, new semantic-loss and cold-read diagnostics run in shadow mode "
                "and cannot by themselves be the reason a paper is held back. These passes "
                "assist the Reviewer; they are not a precondition of its judgment. When the host supplies none, "
                "the Reviewer's own page-by-page inspection is the assessment, and their "
                "absence is never by itself a reason to withhold `done`."
            ),
            evidence_hint=(
                "internal immutable snapshots, isolated rendered-PDF pass, current paper, "
                "and the adjudicated assessment in paper/REVIEW.md"
            ),
        ),
        ChecklistItem(
            id="review.scope",
            statement=(
                "Start from `paper/main.tex`, its rendered output, and `paper/REVIEW.md`, "
                "then follow only direct claim-critical references to code, explicit "
                "configuration, raw rows, evaluators, or primary sources. Do not recursively "
                "crawl historical research files."
            ),
            evidence_hint=(
                "paper/main.tex, rendered output, paper/REVIEW.md, and directly cited "
                "claim-critical evidence"
            ),
        ),
        ChecklistItem(
            id="review.authoritative",
            statement=(
                "Each authoritative review overwrites `paper/REVIEW.md` with the strongest "
                "case for accepting the paper, the scientific, visual, and reader-facing "
                "assessment, the defects a venue reviewer would reject it for, and the "
                "next step. Do not create another review file or review history."
            ),
            evidence_hint="paper/REVIEW.md",
        ),
        ChecklistItem(
            id="review.scientific",
            statement=(
                "Review the complete paper as an independent venue reviewer. Verify the "
                "contribution, fidelity to the executed code, positive controls, strongest "
                "same-information baselines, decisive evidence, citations, whether the "
                "benchmark's labels, balance, and scoring can carry the claim, and whether "
                "all sections and experiments needed by the thesis are present. Reviewer "
                "authority is independent of Engineer or Planner confidence. For narrative "
                "edits, compare the immutable before/after snapshots and veto only a named "
                "lost fact, reasoning step, scope boundary, or coverage carrier—not changed "
                "wording or a valid move into Methods, a table, caption, or Appendix."
            ),
            evidence_hint="paper plus directly cited code, configurations, raw rows, and sources",
        ),
        ChecklistItem(
            id="review.visual",
            statement=(
                "Inspect every rendered page and every figure and table at publication "
                "scale. Any visible overlap, clipping, overflow, connector penetration, "
                "wrong arrow, unreadable label, malformed table, misleading plot, abnormal "
                "whitespace, broken float placement, or inconsistent typography means "
                "the paper does not yet hold visually. A method overview figure must "
                "match the manuscript and the executed code, with editable source "
                "and a legible included vector export. Apply the Method D default "
                "and Method B fallback in paper-framework-figure-studio.md; a justified "
                "fallback is valid, and an API blueprint is not scientific evidence. "
                "Check native editability for D, without imposing the B pipeline "
                "renderer's font or geometry on it. That the paper compiled says "
                "nothing about how it looks. The "
                "whole paper must look publication-ready."
            ),
            evidence_hint="the complete rendered paper and all included figures and tables",
        ),
        ChecklistItem(
            # Keep the public checklist id stable; the implementation of this
            # reader-facing language/argument pass is now PDF-only cold_read.
            id="review.language",
            statement=(
                "The cold reader sees only the rendered PDF and judges centrality, progression, "
                "evidence hierarchy, inference after exact numbers, academic prose, timing, and "
                "visual narrative. "
                "It judges as a venue reviewer would and enforces no abstract length, number "
                "density, or caption format; scientific density, complete controls, and "
                "repeated headline numbers are not defects by themselves."
            ),
            evidence_hint="an isolated workspace containing only paper/main.pdf",
        ),
        ChecklistItem(
            id="review.integrated",
            statement=(
                "Perform one integrated final review of scientific content, visual quality, "
                "language, and conformity to the venue's rules on the current recompiled paper, using any "
                "internal pass results the host supplied and your own inspection where it "
                "did not. Keep all repairs inside Review without moving to an earlier stage."
            ),
            evidence_hint="paper/main.tex and its rendered output/direct dependencies",
        ),
        ChecklistItem(
            id="review.terminal",
            statement=(
                "Review is the final stage. Return done only when the current paper "
                "meets the objective and the venue's standard and `paper/REVIEW.md` "
                "records the final judgment."
            ),
            evidence_hint="paper/REVIEW.md and the current rendered paper",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    return STAGE_CHECKLISTS.get(str(stage).strip().lower(), ())


def _paper_issue(project_root: Path) -> tuple[str, ...]:
    paper = project_root / "paper"
    issues: list[str] = []
    if not (paper / "main.tex").is_file():
        issues.append("paper/main.tex is missing")
    rendered = next(
        (
            paper / name
            for name in ("main.pdf", "main.html")
            if (paper / name).is_file()
        ),
        None,
    )
    if rendered is None:
        issues.append("the rendered paper output is missing")
    elif rendered.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError

            reader = PdfReader(str(rendered))
            if not reader.pages:
                issues.append("paper/main.pdf contains no rendered pages")
        except (OSError, PdfReadError) as exc:
            issues.append(f"paper/main.pdf is not a readable rendered PDF: {exc}")
    else:
        try:
            html = rendered.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(f"paper/main.html is unreadable: {exc}")
        else:
            if len(html.strip()) < 200 or not re.search(
                r"<(?:html|article|main|section)\b",
                html,
                re.IGNORECASE,
            ):
                issues.append("paper/main.html does not contain a rendered paper")
    return tuple(issues)


def _paper_stage_skipped(state_root: Path) -> bool:
    """Whether the Manager recorded the paper stage as skipped for this run.

    A bounded objective may end at the experiment evidence: the Manager then
    advances experiment -> review with paper recorded as skipped, and the
    terminal review certifies the delivered evidence rather than a manuscript.
    """
    try:
        from ...core.pipeline_state import read_pipeline_state

        stages = read_pipeline_state(state_root).get("stages")
        if not isinstance(stages, dict):
            return False
        record = stages.get("paper")
        if not isinstance(record, dict):
            return False
        return str(record.get("status") or "").strip().lower() == "skipped"
    except Exception:  # noqa: BLE001 — unreadable state keeps the strict path
        return False


def _review_document_issues(
    project_root: Path,
) -> tuple[str, ...]:
    review_path = project_root / "paper" / "REVIEW.md"
    try:
        text = review_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    if not text.strip():
        return ("paper/REVIEW.md is missing or empty",)
    return ()


def stage_completion_issues(
    stage: str,
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[str, ...]:
    normalized = str(stage or "").strip().lower()
    root = Path(project_root)
    if normalized == "idea":
        from ...core.pipeline_state import pipeline_state_exists
        from .idea_portfolio import idea_portfolio_completion_issues

        resolved_state_root = Path(state_root or root)
        if not pipeline_state_exists(resolved_state_root):
            log.warning(
                "idea-stage requirements cannot be determined: "
                "PIPELINE_STATE.json is missing at resolved state root %s",
                resolved_state_root,
            )
            return (
                "idea-stage requirements cannot be determined because "
                "PIPELINE_STATE.json is missing at the resolved state root",
            )
        portfolio_issues = idea_portfolio_completion_issues(
            root,
            state_root=resolved_state_root,
        )
        return tuple(portfolio_issues)
    if normalized == "experiment":
        # Experiment is judged on its evidence by the Reviewer and Manager; the
        # research notes are context for Paper, not a completion condition.
        return ()
    if normalized == "paper":
        return _paper_issue(root)
    if normalized == "review":
        if _paper_stage_skipped(Path(state_root or root)):
            return _review_document_issues(root)
        return tuple(
            (
                *_paper_issue(root),
                *_review_document_issues(root),
            )
        )
    return ()


def automatic_stage_completion_ready(
    *,
    stage: str,
    project_root: Path,
    state_root: Path,
) -> bool:
    """Only a completed mandatory portfolio closes without Manager judgment."""
    from .idea_portfolio import portfolio_required

    return bool(
        str(stage or "").strip().lower() == "idea"
        and portfolio_required(state_root)
        and not stage_completion_issues(
            stage,
            project_root,
            state_root=state_root,
        )
    )


def iteration_assessment(
    *,
    stage: str,
    scope: str,
    project_root: Path,
    state_root: Path,
    mission: Any,
    outcome: Any,
) -> IterationAssessment | None:
    """The Reviewer's final judgment stands; nothing re-grades it.

    This hook used to re-open a ``done`` final review whenever the structured
    ``research_result`` grades fell short of the target level (a Reviewer
    calling the work a ``finite_verification`` or leaving novelty
    ``unverified``). One campaign then ran 75 certification missions, each
    reviewed ``done`` and each re-queued, and never completed. A human final
    reviewer who accepts a paper has accepted it; the grades are a summary of
    that judgment, not a second judge.
    """
    _ = (stage, scope, project_root, state_root, mission, outcome)
    return None


RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")
STAGE_ORDER = list(CANONICAL_STAGE_ORDER)
VENUE_DEPENDENT_STAGES = frozenset({"paper", "review"})


def render_stage_checklist_body(
    body: str,
    *,
    project_root: object,
    role: str,
    stage: str,
) -> str:
    _ = (project_root, role, stage)
    return body


def render_full_checklist_body(
    body: str,
    *,
    project_root: object,
    role: str,
) -> str:
    _ = (project_root, role)
    return body


CHECKLIST_STAGE_ORDER = CANONICAL_STAGE_ORDER
CHECKLIST_ITEMS = STAGE_CHECKLISTS
completion_gate = "certified"
MISSION_KIND = "research"
PAPER_MISSION = True
WORKFLOW_MODE = "proportional"
VERIFICATION_STAGE_PROFILES = {
    "idea": "explore",
    "experiment": "develop",
    "paper": "develop",
    "review": "certify",
}
ENGINEER_LIVE_SEARCH_STAGES = frozenset({"idea", "experiment", "paper"})
ENGINEER_STAGE_OPERATIONS = {
    "paper": "author_draft",
    "review": "narrative_edit",
}
REQUIRE_INDEPENDENT_REVIEW = True

_AMBITIOUS_RESEARCH_POLICY = (
    "Build a paper around a real contribution and a result worth defending. "
    "Treat mixed or weak development evidence as a prompt to improve the method, "
    "implementation, evaluator, controls, or experiment. Enter Paper only when "
    "credible evidence, at the scale the claim needs, supports a scientifically "
    "meaningful claim; a claim of superiority must beat the strongest "
    "same-information baseline. When evidence supports "
    "a strong claim, state it plainly instead of burying it under defensive caveats."
)

_PLANNER_RESEARCH_ORCHESTRATION = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Plan only work for the current stage. Research stages are forward-only: "
    "schedule any upstream method, experiment, or paper repair in the current stage "
    "and never request rollback. The project-root research notes, RESEARCH_NOTES.md, "
    "are the sole normal cross-stage context until Review; Review uses paper/main.tex, "
    "its rendered output and direct dependencies, and paper/REVIEW.md."
)

_ENGINEER_RESEARCH_EXECUTION = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Verify current models, benchmark versions, and APIs from live sources instead "
    "of memory. Preserve reproducibility through code, explicit configuration, and raw output, "
    "not extra reporting files. Repair defects in the current stage and never move the "
    "work backward. Keep experiments adaptive and rewrite the research notes, "
    "RESEARCH_NOTES.md, with only what the next stage needs."
)

_REVIEWER_RESEARCH_JUDGEMENT = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Distinguish scientific failure from implementation or evaluator failure. "
    "Keep defects in the current stage and specify the repair; never request rollback. "
    "In Review, overwrite paper/REVIEW.md and create no parallel review record."
)

_MANAGER_RESEARCH_STEWARDSHIP = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Keep the current stage while scheduling repairs. Never move a research project "
    "backward. Advance when the stage's scientific work is done and independently "
    "reviewed; Review is terminal. Judge the science, not the bookkeeping: a missing "
    "or outdated research notes, review note, template detail, or file marker is "
    "repair work for the next round, never by itself a reason to hold a stage. A "
    "Reviewer judgment reached on the current mission is the current review of the "
    "work it inspected; do not demand a separate re-review of edits the Reviewer "
    "already read."
)


def import_legacy_state(*, source_root: object, state_root: object) -> None:
    """Carry pre-isolation research files into the isolated state root.

    Runs once, right after legacy Manager state naming this vertical is copied
    into the new state root: old stage names are rewritten to the current
    four-stage order, and any idea-selection record made under the legacy
    layout is brought along so the campaign does not reopen its portfolio.
    """
    from ...skills.stage_machine import migrate_legacy_research_stage
    from .idea_portfolio import migrate_legacy_idea_selection

    migrate_legacy_research_stage(state_root)
    migrate_legacy_idea_selection(
        source_root,
        state_root=state_root,
        materialize_handoff=False,
    )


def search_altitude_context(project_root: object) -> str:
    """Research context is loaded explicitly by ``prompt_policy``."""
    _ = project_root
    return ""


def role_banner(role: str = "engineer") -> str:
    return {
        "planner": _PLANNER_RESEARCH_ORCHESTRATION,
        "reviewer": _REVIEWER_RESEARCH_JUDGEMENT,
        "engineer": _ENGINEER_RESEARCH_EXECUTION,
        "manager": _MANAGER_RESEARCH_STEWARDSHIP,
    }.get(role, "")


__all__ = [
    "STAGE_ORDER",
    "STAGE_ALIASES",
    "CANONICAL_STAGE_ORDER",
    "STAGE_CHECKLISTS",
    "list_stages",
    "get_stage_checklist",
    "VENUE_DEPENDENT_STAGES",
    "render_stage_checklist_body",
    "render_full_checklist_body",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "WORKFLOW_MODE",
    "VERIFICATION_STAGE_PROFILES",
    "ENGINEER_LIVE_SEARCH_STAGES",
    "ENGINEER_STAGE_OPERATIONS",
    "REQUIRE_INDEPENDENT_REVIEW",
    "role_banner",
    "import_legacy_state",
    "search_altitude_context",
    "render_role_prompt_fragment",
    "render_role_prompt_context",
    "review_purchase_policy",
    "stage_completion_issues",
    "automatic_stage_completion_ready",
    "iteration_assessment",
    "completion_gate",
    "PAPER_MISSION",
]
