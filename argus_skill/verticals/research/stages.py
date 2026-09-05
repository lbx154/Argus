"""Research vertical stage definitions and active role policy.

Research uses one forward-only four-stage pipeline:
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
from .prompt_policy import render_role_prompt_fragment
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
                "choice and project-root `HANDOFF.md` describes the winner plus one "
                "single-line rejection reason for every other route. For a staged "
                "operator-locked paper direction, `HANDOFF.md` instead validates and "
                "positions the supplied idea without inventing a selector or rejected "
                "routes. It replaces prior handoff text rather than appending history. "
                "A direct Idea-only request returns its independently reviewed result "
                "without a cross-stage handoff."
            ),
            evidence_hint="HANDOFF.md and the selected idea in internal pipeline state",
        ),
    ),
    "experiment": _checklist(
        ChecklistItem(
            id="experiment.implementation",
            statement=(
                "Implement the selected mechanism and real strong published baselines "
                "through real entry points. Do not rename a local heuristic after a paper. "
                "Use current model choices where the claim depends on currency, appropriate "
                "public or official benchmarks, and the field's real evaluator. Keep "
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
                "move the pipeline backward. A hypothesis-to-code mapping must name the "
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
                "global experiment plan is required, and experiment designs do not "
                "prescribe repeated runs across random seeds — one well-configured run "
                "per condition is the default, with compute spent on decisive "
                "comparisons instead."
            ),
            evidence_hint="executed commands/configuration and raw experimental outputs",
        ),
        ChecklistItem(
            id="experiment.paper_bar",
            statement=(
                "Advance to Paper only when mechanism-relevant wins clearly exceed losses, "
                "headline and primary comparisons win, and the strongest same-information "
                "baseline is beaten. Comparisons must include real strong published "
                "baselines rather than renamed local heuristics, use current models and "
                "appropriate public or official benchmarks where relevant, and pass a "
                "positive control through the real evaluator. Otherwise improve the method "
                "or experiment in the current Experiment stage."
            ),
            evidence_hint="claim-bearing comparisons, controls, and direct raw outputs",
        ),
        ChecklistItem(
            id="experiment.repair",
            statement=(
                "Treat method, experiment, evaluator, and evidence defects as repair work "
                "inside Experiment. Keep the selected idea and current stage; never request "
                "a rollback or convert unfinished development into a negative-result paper."
            ),
            evidence_hint="repaired work products and the next decisive comparison",
        ),
        ChecklistItem(
            id="experiment.handoff",
            statement=(
                "When the Paper entry bar is met, overwrite project-root `HANDOFF.md` with "
                "the thesis, winning comparisons, strongest baseline, essential losses or "
                "limits, figures/data to use, and the minimum reproducibility pointers "
                "needed to write the paper. Classify the complete evidence as headline, "
                "mechanism, disambiguating control, scope-changing, or completeness "
                "evidence, and name its canonical and repeat locations."
            ),
            evidence_hint="HANDOFF.md",
        ),
    ),
    "paper": _checklist(
        ChecklistItem(
            id="paper.argument",
            statement=(
                "Produce a complete paper draft led by the contribution and strongest "
                "result. Include every claim-bearing experiment, intended figure and "
                "table, citation, and venue-required section. Select and package evidence "
                "by its headline, mechanism, disambiguating-control, scope-changing, or "
                "completeness role: keep complete matrices in Methods, tables, or the "
                "Appendix while prose interprets the comparisons that change the current "
                "inference. Do not organize it as an experiment chronology or ship a "
                "development shortfall as a negative-result report."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="paper.presentation",
            statement=(
                "Preserve the five-sentence, at-least-170-word abstract contract, exact "
                "headline numbers in the major reader-facing locations where they establish "
                "the claim, and a numerical takeaway in every figure and table caption. "
                "The same headline number may recur for a different section role; do not "
                "apply a mechanical repetition cap or recite the same full result matrix "
                "in every location."
            ),
            evidence_hint="paper/main.tex and its rendered figures and tables",
        ),
        ChecklistItem(
            id="paper.work_products",
            statement=(
                "The manuscript, bibliography, figures, included source files, and rendered "
                "output are present, mutually consistent, and compile under the selected "
                "venue's current official rules. Method pipelines use an editable SVG "
                "grounded in the manuscript and executed code, compact horizontal and "
                "staggered geometry, Times New Roman, and an included vector PDF export. "
                "Reuse a suitable existing figure; draw only when needed. Default PDF "
                "placement is after Introduction, preferably on page 2 or 3, subject to "
                "the author kit and actual Introduction length. "
                "Final scientific review, strict visual "
                "inspection, and academic-language polishing happen only in Review."
            ),
            evidence_hint="paper/main.tex, rendered output, bibliography, figures, and includes",
        ),
        ChecklistItem(
            id="paper.handoff",
            statement=(
                "Keep project-root `HANDOFF.md` as the single upstream context for Paper, "
                "rewritten rather than accumulated. Do not create parallel project-visible "
                "handoff files."
            ),
            evidence_hint="HANDOFF.md",
        ),
    ),
    "review": _checklist(
        ChecklistItem(
            id="review.parallel",
            statement=(
                "Before narrative editing, preserve an immutable source/PDF snapshot in "
                "internal mission state. After the fresh-context edit, run three independent "
                "read-only passes in parallel: before/after scientific semantic-loss, strict "
                "rendered visual quality, and a cold read whose isolated input contains only "
                "the current rendered PDF. Keep pass results internal; the integrated Reviewer "
                "records their adjudicated result only in `paper/REVIEW.md`. Until calibration "
                "promotes them, new semantic-loss and cold-read diagnostics run in shadow mode "
                "and cannot be the sole reason to block."
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
                "accept case, scientific/visual/reader-facing assessment, reject-level defects, "
                "and next action. Do not create another review file or review history."
            ),
            evidence_hint="paper/REVIEW.md",
        ),
        ChecklistItem(
            id="review.scientific",
            statement=(
                "Review the complete paper as an independent venue reviewer. Verify the "
                "contribution, fidelity to the executed code, positive controls, strongest "
                "same-information baselines, decisive evidence, citations, and whether all "
                "sections and experiments needed by the thesis are present. Reviewer "
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
                "whitespace, broken float placement, or inconsistent typography blocks "
                "acceptance. Method pipelines must match the manuscript and executed "
                "code, with compact horizontal, staggered SVG geometry, Times New Roman, "
                "and a legible included vector export. A successful render alone is not "
                "visual acceptance. The whole paper must look publication-ready."
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
                "It does not penalize scientific density, complete controls, five-sentence/170-word "
                "abstracts, numerical captions, or repeated headline numbers by themselves."
            ),
            evidence_hint="an isolated workspace containing only paper/main.pdf",
        ),
        ChecklistItem(
            id="review.integrated",
            statement=(
                "After the three internal passes are adjudicated and the paper is recompiled, "
                "perform one integrated final review of scientific content, visual quality, "
                "language, and venue compliance. Keep all repairs inside Review without "
                "moving to an earlier stage."
            ),
            evidence_hint="paper/main.tex and its rendered output/direct dependencies",
        ),
        ChecklistItem(
            id="review.terminal",
            statement=(
                "Review is the terminal certified stage. Return done only when the current "
                "paper clears the objective and venue bar and `paper/REVIEW.md` records the "
                "authoritative verdict."
            ),
            evidence_hint="paper/REVIEW.md and the current rendered paper",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    return STAGE_CHECKLISTS.get(str(stage).strip().lower(), ())


def _handoff_issue(project_root: Path, stage: str) -> tuple[str, ...]:
    _ = stage
    path = project_root / "HANDOFF.md"
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        text = ""
    if not text.strip():
        return ("project-root HANDOFF.md is missing or empty",)
    return ()


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
        return tuple((*portfolio_issues, *_handoff_issue(root, "idea")))
    if normalized == "experiment":
        return _handoff_issue(root, normalized)
    if normalized == "paper":
        return tuple(
            (
                *_paper_issue(root),
                *_handoff_issue(root, "paper"),
            )
        )
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


def iteration_assessment(
    *,
    stage: str,
    scope: str,
    project_root: Path,
    state_root: Path,
    mission: Any,
    outcome: Any,
) -> IterationAssessment | None:
    """Keep final-review shortfalls as repair work in the current stage."""
    _ = (project_root, mission)
    if (
        str(stage or "").strip().lower() != "review"
        or str(scope or "").strip().lower().replace("-", "_")
        != "final_submission"
    ):
        return None

    from ...core.research_contract import (
        normalize_research_result,
        research_completion_issue,
        resolve_research_target_level,
    )

    target = resolve_research_target_level(state_root)
    raw_result = getattr(outcome, "research_result", None)
    issue = research_completion_issue(
        raw_result,
        research_target_level=target,
    )
    if not issue:
        return None
    result = normalize_research_result(raw_result)
    detail = (
        "; ".join([*result["evidence"], *result["limitations"]])[:600]
        if result is not None
        else "the authoritative review did not provide a valid research result"
    )
    return IterationAssessment(
        shortfall=issue,
        objective=(
            "Keep the pipeline in Review. Repair the specific method, experiment, "
            "paper, or presentation defect that blocks certification, then overwrite "
            "paper/REVIEW.md with the next authoritative verdict. Do not roll back.\n"
            f"Blocking issue: {issue}.\nEvidence: {detail}"
        ),
    )


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
    "implementation, evaluator, controls, or experiment. Enter Paper only after "
    "mechanism-relevant wins clearly exceed losses, the headline comparisons win, "
    "and the strongest same-information baseline is beaten. When evidence supports "
    "a strong claim, state it plainly instead of burying it under defensive caveats."
)

_PLANNER_RESEARCH_ORCHESTRATION = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Plan only work for the current stage. Research stages are forward-only: "
    "schedule any upstream method, experiment, or paper repair in the current stage "
    "and never request rollback. Use project-root HANDOFF.md as the sole normal "
    "cross-stage context until Review; Review uses paper/main.tex, its rendered output "
    "and direct dependencies, and paper/REVIEW.md."
)

_ENGINEER_RESEARCH_EXECUTION = (
    _AMBITIOUS_RESEARCH_POLICY
    + " Verify current models, benchmark versions, and APIs from live sources instead "
    "of memory. Preserve reproducibility through code, explicit configuration, and raw output, "
    "not extra reporting files. Repair defects in the current stage and never move the "
    "research pipeline backward. Keep experiments adaptive and rewrite HANDOFF.md with "
    "only what the next stage needs."
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
    "backward. Advance only when the active checklist is satisfied; Review is terminal."
)


def import_legacy_state(*, source_root: object, state_root: object) -> None:
    """Carry pre-isolation research artifacts into the isolated state root.

    Runs once, right after legacy Manager state naming this vertical is copied
    into the new state root: old stage names are rewritten to the current
    four-stage pipeline, and any idea-selection record made under the legacy
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
    "review_purchase_policy",
    "stage_completion_issues",
    "iteration_assessment",
    "completion_gate",
    "PAPER_MISSION",
]
