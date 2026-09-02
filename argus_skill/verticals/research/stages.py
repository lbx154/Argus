"""Research vertical stage definitions and active role policy.

Research uses one forward-only five-stage pipeline:
``idea -> build -> experiment -> paper -> review``.
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
    "build",
    "experiment",
    "paper",
    "review",
)

# Aliases remain useful at parse boundaries, while durable migration is invoked
# explicitly at Manager/runtime entry points.
STAGE_ALIASES = {
    "research": "idea",
    "plan": "build",
    "benchmark": "build",
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
                "For a new broad publishable or doctoral direction, complete exactly "
                "twelve source-only routes, twelve independent route reviews, and one "
                "selector. Candidate execution is forbidden during selection. Full "
                "working outputs stay under internal `.argus` team storage."
            ),
            evidence_hint="internal `.argus/teams/...` task outputs",
        ),
        ChecklistItem(
            id="idea.selection",
            statement=(
                "The selector makes one resumable choice. Project-root `HANDOFF.md` "
                "describes the winning idea in enough detail to build it and gives one "
                "single-line rejection reason for every other route. It replaces prior "
                "handoff text rather than appending history."
            ),
            evidence_hint="HANDOFF.md and the selected idea in internal pipeline state",
        ),
    ),
    "build": _checklist(
        ChecklistItem(
            id="build.implementation",
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
            id="build.fidelity",
            statement=(
                "Trace the actual call path and confirm the method, baseline, controls, "
                "information boundary, and evaluator test the selected idea. Repair "
                "implementation or setup defects in Build; do not reopen selection or "
                "move the pipeline backward. A hypothesis-to-code mapping must name the "
                "executed quantities and path rather than merely matching labels."
            ),
            evidence_hint="implemented entry points and their direct test output",
        ),
        ChecklistItem(
            id="build.positive_control",
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
            id="build.handoff",
            statement=(
                "Overwrite project-root `HANDOFF.md` with only the minimum implementation, "
                "configuration, baseline, evaluator, known-risk, and next-run context "
                "Experiment needs."
            ),
            evidence_hint="HANDOFF.md",
        ),
    ),
    "experiment": _checklist(
        ChecklistItem(
            id="experiment.adaptive",
            statement=(
                "Run an adaptive programme: every executed run is reproducible from its "
                "code, explicit configuration, and raw output, while methods, baselines, "
                "benchmarks, controls, and next experiments may change in response to "
                "development evidence. No frozen global experiment plan is required."
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
                "needed to write the paper."
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
                "table, citation, and venue-required section. Do not organize it as an "
                "experiment chronology or ship a development shortfall as a "
                "negative-result report."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="paper.work_products",
            statement=(
                "The manuscript, bibliography, figures, included source files, and rendered "
                "output are present, mutually consistent, and compile under the selected "
                "venue's current official rules. Final scientific review, strict visual "
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
                "Run three independent read-only passes on the same current paper in "
                "parallel: scientific completeness, strict rendered visual quality, and "
                "academic language. Record their combined findings only in "
                "`paper/REVIEW.md`; create no additional project review files."
            ),
            evidence_hint="the three-pass assessment in paper/REVIEW.md and the current paper",
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
                "accept case, scientific/visual/language assessment, reject-level defects, "
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
                "authority is independent of Engineer or Planner confidence."
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
                "acceptance. The whole paper must look publication-ready."
            ),
            evidence_hint="the complete rendered paper and all included figures and tables",
        ),
        ChecklistItem(
            id="review.language",
            statement=(
                "The read-only language pass reports precise proposed changes for "
                "confident, accurate academic prose. The single Engineer repair then "
                "removes defensive qualifier boilerplate, experiment chronology, internal "
                "workflow language, repeated caveats, and integrity self-praise while "
                "preserving every supported claim and technical meaning."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="review.integrated",
            statement=(
                "After the three parallel passes are repaired and the paper is recompiled, "
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
    path = project_root / "HANDOFF.md"
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        text = ""
    if not text.strip():
        return ("project-root HANDOFF.md is missing or empty",)
    marker = f"# HANDOFF — {str(stage).strip().upper()}"
    first_line = next(
        (line.strip() for line in text.splitlines() if line.strip()),
        "",
    )
    if first_line != marker:
        return (
            f"project-root HANDOFF.md is stale for {stage}: expected first line "
            f"{marker!r}",
        )
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


def _paper_quality_issues(
    project_root: Path,
    state_root: Path,
) -> tuple[str, ...]:
    from .integrity_check import check_citations
    from .paper_structural_minimums import validate_paper_structural_minimums

    issues = [
        f"[citation_integrity:{issue.code}] {issue.message}"
        for issue in check_citations(project_root)
        if issue.blocking
    ]
    report = validate_paper_structural_minimums(
        project_root,
        state_root=state_root,
    )
    issues.extend(f"[{issue.code}] {issue.detail}" for issue in report.issues)
    return tuple(dict.fromkeys(issues))


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

    issues: list[str] = []
    lowered = text.lower()
    if "**verdict:** done" not in lowered:
        issues.append("paper/REVIEW.md does not record an authoritative done verdict")
    required_sections = (
        "## scientific, visual, and language assessment",
        "## strongest accept case",
        "## reject-level issues",
        "## next action",
    )
    for section in required_sections:
        if section not in lowered:
            issues.append(f"paper/REVIEW.md is missing {section}")

    assessment_match = re.search(
        r"(?ims)^## scientific, visual, and language assessment\s*(.*?)(?=^## |\Z)",
        text,
    )
    assessment = assessment_match.group(1).lower() if assessment_match else ""
    accept_match = re.search(
        r"(?ims)^## strongest accept case\s*(.*?)(?=^## |\Z)",
        text,
    )
    accept_case = accept_match.group(1).strip() if accept_match else ""
    if len(" ".join(accept_case.split())) < 40:
        issues.append("paper/REVIEW.md strongest accept case is not substantive")
    for label in ("scientific:", "visual:", "language:"):
        if label not in assessment:
            issues.append(f"paper/REVIEW.md is missing the final {label[:-1]} assessment")
    return tuple(dict.fromkeys(issues))


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
    if normalized in {"build", "experiment"}:
        return _handoff_issue(root, normalized)
    if normalized == "paper":
        resolved_state_root = Path(state_root or root)
        return tuple(
            (
                *_paper_issue(root),
                *_paper_quality_issues(root, resolved_state_root),
                *_handoff_issue(root, "paper"),
            )
        )
    if normalized == "review":
        resolved_state_root = Path(state_root or root)
        return tuple(
            (
                *_paper_issue(root),
                *_paper_quality_issues(root, resolved_state_root),
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
    "build": "develop",
    "experiment": "develop",
    "paper": "develop",
    "review": "certify",
}
ENGINEER_LIVE_SEARCH_STAGES = frozenset({"idea", "build", "experiment", "paper"})
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
    "REQUIRE_INDEPENDENT_REVIEW",
    "role_banner",
    "search_altitude_context",
    "render_role_prompt_fragment",
    "review_purchase_policy",
    "stage_completion_issues",
    "iteration_assessment",
    "completion_gate",
    "PAPER_MISSION",
]
