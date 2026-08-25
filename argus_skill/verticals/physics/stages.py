"""Dynamic-path vertical for physics tasks with a research-paper terminal stage.

The five stages are deliberately coarse. Theoretical derivation, numerical
simulation, data analysis, literature synthesis, and experiment design are
*methods* selected for the physical task at hand, not mandatory pipeline
stages. This is a lightweight, Argus-native physics vertical — not a large
physics research framework: it ships the stage contract, role framing, and
reviewer checklists, and leaves heavy checker/solver/literature machinery out.

Stage semantics:

* ``scope``   — pin down the original physics task: system, domain, observables,
  task type, success criterion, and a feasible route.
* ``model``   — pin down the model and evidence plan: variables, units,
  equations/data sources, assumptions, approximation range, BC/IC, and the
  validation target.
* ``execute`` — do the physics: derive, compute, analyze data, synthesize
  literature, or judge experiment feasibility — producing *bounded* evidence.
* ``review``  — independently audit physical fidelity, model validity, units,
  BC/IC, numerical/data/literature evidence, and the boundary of every claim.
* ``manuscript`` — MANDATORY terminal stage: organize the reviewed evidence into
  a standard, discipline-agnostic research-paper package delivered in three
  layers — a machine-checkable source layer (MANUSCRIPT.md, >=6 numbered figures
  + legends, >=8 real references, a CLAIMS.csv ledger, REPRODUCIBILITY.md,
  METHODS_DETAIL.md, REVIEW.md), a LaTeX-compiled paper layer (MANUSCRIPT.tex/pdf,
  SUPPLEMENT.tex/pdf, PAPER_BUILD_LOG.md), and an OPTIONAL presentation layer
  (HTML_DEMO/PRESENTATION) that never gates — and audit paper structure,
  figure->claim binding, citations, equations, tables, reproducibility, and
    no-overclaim. Enforced before terminal completion by the typed
    ``stage_completion_issues`` hook backed by ``manuscript.py`` (no optional
    mode, no marker file, no env var).
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from .manuscript import PAPER_AUDIT_HEADING

STAGE_ORDER = ("scope", "model", "execute", "review", "manuscript")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"

# Physics missions end through the ordinary reviewer-certified final-stage path.
# They are neither paper-submission missions nor metric-optimization campaigns.
completion_gate = "none"

# ``manuscript`` is the mandatory terminal stage: a completed physics mission's
# deliverable is a standard research-paper package, not a scope/model/execute/
# review log, judged by the L2 Reviewer against the CHECKLIST_ITEMS below and
# the always-fail-closed manuscript verifier in ``manuscript.py`` (no optional
# mode, no marker file, no env var) that the agent is instructed to run itself.

# ``scope`` runs the Literature Positioning gate in ADVISORY mode: it verifies the
# agent's PRIOR_WORK_MATRIX.csv artifact, writes a machine-readable failure list +
# repair context (research/LITERATURE_GATE_*), but ALWAYS exits 0 so it never
# blocks scope->model. Its failures are fed into the next scope/model prompt (via
# ``role_banner``) and its RESULT feeds the review/claims discipline.
#
# ``model`` runs the Theory Capability gate in ADVISORY mode (never blocks
# model->execute): it verifies DOMAIN_CLASSIFICATION.json + THEORY_OPPORTUNITY_AUDIT.csv
# and feeds failures into the next-round repair context.
#
# ``execute`` runs the Numerical Capability gate in ADVISORY mode (never blocks
# execute->review): it verifies NUMERICAL_STUDY_PLAN.csv and cross-checks CLAIMS.csv
# (robustness / phase-diagram claims need matching numerical evidence).
#
# ``review`` runs the Novelty gate and the Paper-Type classifier in ADVISORY mode
# (never blocks review->manuscript). The Paper-Type gate CONSUMES the literature /
# novelty / numerical gate results: a paper cannot be an original research article
# candidate unless those gates support it. The old Novelty-Seeking table gate is
# retired: fixed idea counts, exact columns, and numeric scores rewarded table
# completion rather than discovery. Planner and Reviewer now judge whether routes
# are materially different and whether feedback justifies a pivot. The
# Manuscript-Package contract gate remains ADVISORY and surfaces the SAME
# deterministic contract as the terminal ``manuscript`` checker once a paper
# package exists, so ``role_banner`` injects an executable repair loop into the
# next round.
#
# ``execute`` + ``review`` run the Auto-Downgrade gate (ADVISORY): when the run has
# exhausted the allowed effort at the current innovation tier (model<->execute churn,
# pivot cap, repeated reviewer rejections / blockers, a closure artifact exists, or a
# hygiene closure-loop), it proposes+applies a one-rung tier downgrade (S->A->B->C->D)
# and surfaces a reviewer-ratification directive. Never blocks a stage.
#
# All advisory gates above are invoked directly by the agent (per the prose
# instructions in ``role_banner`` below) and by ``skills.research_gates``
# (``render_active_repair_blocks`` scans on-disk ``*_GATE_STATE.json``); none of
# them is wired through a shell-command registry. The terminal manuscript
# contract is different: the stage machine calls ``stage_completion_issues``
# before it can mark the final stage done.

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.faithful-goal",
            statement=(
                "The original physics goal is stated faithfully, with the physical "
                "system, its domain/regime, and the observables of interest explicit."
            ),
            evidence_hint="a faithful task statement naming the system, domain, and observables",
        ),
        ChecklistItem(
            id="scope.task-type-success",
            statement=(
                "The task type and success criterion are explicit: derive, simulate, "
                "analyze data, synthesize literature, design an experiment, estimate, "
                "or make bounded progress — with an honest criterion for completion."
            ),
            evidence_hint="a declared task type and an honest, checkable success criterion",
        ),
        ChecklistItem(
            id="scope.dynamic-route",
            statement=(
                "A feasible route is chosen from the physical structure of the task: "
                "theoretical derivation, numerical simulation, data analysis, "
                "literature synthesis, experiment design, or a bounded negative result — not a "
                "forced fixed pipeline."
            ),
            evidence_hint="a task-specific route with reasons for included and skipped methods",
        ),
    ),
    "model": (
        ChecklistItem(
            id="model.variables-equations",
            statement=(
                "Variables, parameters, units, and the governing equations, model, or "
                "data sources are explicit and internally consistent."
            ),
            evidence_hint="a variable/parameter table with units and the equations or data sources",
        ),
        ChecklistItem(
            id="model.assumptions-bcic",
            statement=(
                "Assumptions, the validity range of each approximation, and the "
                "boundary and initial conditions are stated explicitly."
            ),
            evidence_hint="listed assumptions, approximation ranges, and BC/IC",
        ),
        ChecklistItem(
            id="model.validation-target",
            statement=(
                "The observables and the validation target are declared: an analytic "
                "limit, a baseline, a residual, convergence, an uncertainty budget, or "
                "an explicit evidence boundary."
            ),
            evidence_hint="a named validation target the execute stage can be checked against",
        ),
    ),
    "execute": (
        ChecklistItem(
            id="execute.bounded-evidence",
            statement=(
                "The execute work produced real, bounded evidence — a derivation, a "
                "computation, a data analysis, a literature synthesis, or a feasibility "
                "judgment — rather than an unsupported assertion."
            ),
            evidence_hint="explicit derivations, reproducible runs, analyzed data, or cited synthesis",
        ),
        ChecklistItem(
            id="execute.provenance",
            statement=(
                "Every numerical, data, and literature claim carries provenance: the "
                "source, the code/run, or the reference it came from."
            ),
            evidence_hint="run logs, dataset ids, or resolvable citations for each claim",
        ),
        ChecklistItem(
            id="execute.no-overclaim",
            statement=(
                "Finite simulation or toy data is not presented as a proof of a "
                "universal or infinite physical statement; the tested regime and the "
                "evidentiary limit are stated."
            ),
            evidence_hint="the tested range plus the precise limit of what it supports",
        ),
        ChecklistItem(
            id="execute.honest-boundary",
            statement=(
                "When a critical condition is missing (data, apparatus, or full-text "
                "literature), the work returns an explicit blocker or a clearly bounded "
                "surrogate instead of pretending to be complete."
            ),
            evidence_hint="an explicit blocker / bounded-surrogate note naming the missing condition",
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.no-system-drift",
            statement=(
                "There is no physical-system drift: the audited work still concerns "
                "the original system, regime, and observables."
            ),
            evidence_hint="a comparison of the reviewed result against the original scoped system",
        ),
        ChecklistItem(
            id="review.no-workflow-drift",
            statement=(
                "There is no agent-workflow or meta-paper drift and no toy-overclaim: "
                "the result is about the physics, not about the pipeline that produced it."
            ),
            evidence_hint="confirmation that claims are physical, not workflow or metadata artifacts",
        ),
        ChecklistItem(
            id="review.units-bcic",
            statement=(
                "Units and dimensions are checked where they apply, and boundary and "
                "initial conditions are checked where they apply."
            ),
            evidence_hint="a dimensional-consistency and BC/IC check, or a reason it does not apply",
        ),
        ChecklistItem(
            id="review.evidence-boundary",
            statement=(
                "The numerical, data, and literature evidence boundary is explicit, "
                "and metadata-only sources are not treated as full text."
            ),
            evidence_hint="labeled evidence levels: full-text, excerpt, code/data, metadata-only, unavailable",
        ),
        ChecklistItem(
            id="review.claim-status",
            statement=(
                "Every final claim is labeled supported, partial, inconclusive, "
                "or unknown, with the remaining gaps stated."
            ),
            evidence_hint="claim-by-claim status labels and stated remaining gaps",
        ),
    ),
    "manuscript": (
        ChecklistItem(
            id="manuscript.paper-package",
            statement=(
                "The terminal deliverable is a standard research-paper package, not a "
                "scope/model/execute/review log: MANUSCRIPT.md with Abstract/Summary, "
                "Introduction, Background/Related Work, Model/Theory/System, Methods, "
                "Results, Discussion, Limitations, Conclusion, References, and Data & "
                "Code Availability."
            ),
            evidence_hint="MANUSCRIPT.md with every standard research-paper section present",
        ),
        ChecklistItem(
            id="manuscript.figures-legends",
            statement=(
                ">= 6 numbered figures (figures/fig1_*, ... fig6_*), each with a formal "
                "legend in FIGURE_LEGENDS.md: title, panel labels, axes/units, "
                "uncertainty/statistics where applicable, data/script provenance, and "
                "the claim it supports."
            ),
            evidence_hint=">= 6 numbered figures plus FIGURE_LEGENDS.md with per-figure legends",
        ),
        ChecklistItem(
            id="manuscript.references",
            statement=(
                ">= 8 real, resolvable references (REFERENCES.bib or references.md) that "
                "match in-text citations; unverifiable sources are marked "
                "NEEDS_VERIFICATION rather than fabricated into the reference list."
            ),
            evidence_hint=">= 8 resolvable references consistent with in-text citations",
        ),
        ChecklistItem(
            id="manuscript.claims-ledger",
            statement=(
                "CLAIMS.csv binds every headline claim to an equation/figure/table/"
                "script/dataset/citation with a claim_type, evidence, a "
                "supported/partial/inconclusive/unknown status, and a boundary. "
                "Its header MUST be exactly these 8 columns, in order (no synonyms; "
                "'claim' and 'evidence' are rejected and must be renamed): "
                "claim_id,claim_text,claim_type,evidence_type,evidence_pointer,status,boundary,reviewer_notes."
            ),
            evidence_hint=(
                "CLAIMS.csv whose header is exactly "
                "claim_id,claim_text,claim_type,evidence_type,evidence_pointer,status,boundary,reviewer_notes"
            ),
        ),
        ChecklistItem(
            id="manuscript.reproducibility",
            statement=(
                "REPRODUCIBILITY.md and METHODS_DETAIL.md make the results reproducible: "
                "exact commands, software versions, seeds, parameter ranges, input and "
                "generated data, figure-generation scripts, runtime, and agent/human "
                "provenance."
            ),
            evidence_hint="REPRODUCIBILITY.md + METHODS_DETAIL.md sufficient to reproduce",
        ),
        ChecklistItem(
            id="manuscript.no-overclaim",
            statement=(
                "No claim over-extends its evidence: finite numerics are not universal "
                "proofs, synthetic/toy results are not real-system validation, and "
                "novelty/discovery claims are bound to evidence or downgraded. (A "
                "manager-facing HTML_DEMO/PRESENTATION page is an OPTIONAL presentation "
                "layer that never gates.)"
            ),
            evidence_hint="an evidence-bounded claim set with no finite->universal or synthetic->real leap",
        ),
        ChecklistItem(
            id="manuscript.paper-composition",
            statement=(
                "The paper composition layer exists and compiles: MANUSCRIPT.tex -> "
                "MANUSCRIPT.pdf and SUPPLEMENT.tex -> SUPPLEMENT.pdf, with a "
                "PAPER_BUILD_LOG.md. Default profile physics_two_column_article (two-column "
                "article layout, not a revtex dependency); broad_science_review_draft only "
                "on request. >=4 numbered LaTeX display equations (each \\label'd, >=3 "
                "'Eq. (n)' references), >=2 main + >=2 supplementary tables, a real "
                "References section, and section thickness in the target bands (Introduction "
                ">=600 and Results >=1200 words at minimum)."
            ),
            evidence_hint="compiled MANUSCRIPT.pdf + SUPPLEMENT.pdf with equations, tables, and a References section",
        ),
        ChecklistItem(
            id="manuscript.paper-language-polish",
            statement=(
                "The paper main text reads as scientific prose, not an engineering report: "
                "one consistent numbered-citation style resolved via \\cite (no leaked "
                "BibTeX keys); no engineering/workflow tokens (artifact, verifier, "
                "stage_check, project_done, Argus, workspace, CLAIMS.csv, REVIEW.md, "
                "METHODS_DETAIL.md, REPRODUCIBILITY.md) and no scripts/ / data/ / .json / "
                ".csv paths outside Data/Code availability or the Supplement; every figure "
                "cited as 'Fig. N' near its discussion; captions <=250 words; availability "
                "statements free of absolute paths and long command blocks."
            ),
            evidence_hint="paper-language main text free of workflow tokens, with rendered citations and equations",
        ),
        ChecklistItem(
            id="manuscript.review-audit",
            statement=(
                "REVIEW.md contains a section titled exactly '## " + PAPER_AUDIT_HEADING +
                "' recording the paper-layer verdicts (MANUSCRIPT.pdf + SUPPLEMENT.pdf "
                "present; citations, equations, tables, figures, availability, Supplement "
                "cross-references, and no-overclaim). This heading appears in REVIEW.md "
                "only, never in the paper."
            ),
            evidence_hint="a '" + PAPER_AUDIT_HEADING + "' section in REVIEW.md covering every paper-layer check",
        ),
    ),
}


def _current_stage(project_root: object) -> str:
    """Best-effort read of the current pipeline stage (for stage-entry contracts)."""
    if project_root is None:
        return ""
    try:
        from ...core.pipeline_state import read_pipeline_state

        data = read_pipeline_state(project_root)
        return str(data.get("current_stage", "") or "")
    except Exception:  # noqa: BLE001
        return ""


_STAGE_ENTRY_CONTRACTS: dict[str, str] = {
    "scope": (
        "## Scope focus\n"
        "Define the physical system, regime, observables, and success criterion. "
        "Search current primary literature far enough to identify the closest work "
        "and a real separation; use whatever notes make that judgment clear, not a "
        "fixed matrix or paper count.\n"
    ),
    "model": (
        "## Model focus\n"
        "State variables, units, equations or data sources, assumptions, validity "
        "ranges, and boundary/initial conditions. Choose theory tools because the "
        "problem needs them, not to fill a capability inventory.\n"
    ),
    "execute": (
        "## Execute focus\n"
        "Produce claim-bearing derivation, computation, data analysis, or experiment "
        "evidence. Use convergence, scans, controls, and uncertainty in proportion "
        "to the claim. Treat weak or negative feedback as a reason to repair or "
        "change the route, not as a paper conclusion.\n"
    ),
    "review": (
        "## Review focus\n"
        "Independently judge physical correctness, novelty against current closest "
        "work, significance, and the evidence boundary of each claim. Replan when "
        "the central idea is too weak; do not convert uncertainty into paperwork.\n"
    ),
    "manuscript": (
        "## Manuscript focus\n"
        "Write a conventional paper around one central physical insight. Use "
        "claim-driven figures, real citations, reproducible methods, and concise "
        "limitations. The Reviewer judges venue quality from the paper itself; no "
        "fixed figure count, exact ledger schema, or internal-process narrative "
        "proves readiness.\n"
    ),
}


def stage_entry_contract(stage: str) -> str:
    """Return the stage-entry contract text for ``stage`` (empty if none)."""
    return _STAGE_ENTRY_CONTRACTS.get((stage or "").strip().lower(), "")


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    """Return deterministic blockers before the stage can be marked done."""
    if (stage or "").strip().lower() != "manuscript":
        return ()
    from .manuscript import verify_all_deliverables

    return tuple(verify_all_deliverables(project_root))


def _mode_banner(project_root: object = None) -> str:
    """Short strategy note for the active innovation tier."""
    try:
        from .downgrade import read_current_tier
        tier = read_current_tier(project_root)
        stretch = ""
        try:
            from .mode_config import is_original_research_required

            if is_original_research_required():
                stretch = " The operator requested original research, so pursue a real prior-work-separated contribution."
        except Exception:  # noqa: BLE001
            pass
        return (
            f"## Physics strategy\nActive tier: {tier}.{stretch} Seek materially "
            "different high-upside routes and current evidence. Let feedback "
            "strengthen, combine, pivot, or retire them; never replace exploration "
            "with a scorecard.\n"
        )
    except Exception:  # noqa: BLE001 — mode banner must never break the role banner
        return ""


def role_banner(role: str, project_root: object = None) -> str:
    """Frame each role around dynamic physics work and honest, bounded evidence.

    When ``project_root`` is given and a manuscript repair context exists (the
    terminal deterministic verifier failed on a prior round), the exact failure
    list + forced repair instructions are appended so the next agent round gets
    them verbatim — see ``argus_skill.skills.manuscript_repair``.
    """
    repair = ""
    if project_root is not None:
        try:
            from ...skills.manuscript_repair import read_repair_state, render_repair_block

            block = render_repair_block(read_repair_state(project_root))
            if block:
                repair = "\n\n" + block
        except Exception:  # noqa: BLE001 — repair context must never break the banner
            repair = ""
    # Gate-forward: prepend the CURRENT stage's entry contract + run-mode notice so the
    # agent builds to the gate standard from the start (not only after a failed exit check).
    entry = stage_entry_contract(_current_stage(project_root))
    if entry:
        repair = "\n\n" + entry + repair
    mode = _mode_banner(project_root)
    if mode:
        repair = "\n\n" + mode + repair
    common = (
        "MISSION TYPE: PHYSICS. Let the physical question choose theory, simulation, "
        "data, literature, or experiment work. Keep units, assumptions, evidence, and "
        "claim boundaries honest. No fixed table, artifact count, or gate output proves "
        "scientific value.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Choose the route from the physics and expected information gain. Keep "
            "multiple mechanisms alive when uncertainty warrants it, and turn weak "
            "feedback into a changed route rather than more paperwork. Delegate "
            "cohesive claim-bearing work and reuse settled evidence."
        ) + repair
    if role_norm == "engineer":
        return common + (
            "Execute the most informative physical route, then follow the feedback. "
            "Make equations, units, assumptions, and boundary/initial conditions "
            "explicit. Report residuals, convergence, uncertainty, provenance, and "
            "evidence limits when they matter to the claim. A toy, metadata record, "
            "or workflow artifact is not physical evidence."
        ) + repair
    if role_norm == "reviewer":
        return common + (
            "Independently judge physical-system fidelity, dimensional consistency, "
            "boundary/initial conditions, numerical or data evidence, uncertainty, "
            "novelty, significance, and claim boundaries. Reject fabricated, "
            "metadata-only, or agent-workflow evidence, but do not demand fixed "
            "tables, counts, or certificates. Ask for replan when the idea is weak."
        ) + repair
    return common + repair


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "stage_completion_issues",
    "stage_entry_contract",
]
