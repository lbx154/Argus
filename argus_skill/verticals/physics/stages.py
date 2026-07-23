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
  no-overclaim. Enforced by ``manuscript.py`` (no optional mode, no marker file,
  no env var).
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
# candidate unless those gates support it. ``review`` also runs the Novelty-Seeking
# Loop gate in ADVISORY mode (only ENFORCES in original-research-required mode:
# >=10 scored candidate directions with the top 2-3 selected and verified) and the
# Manuscript-Package contract gate in ADVISORY mode, which surfaces the SAME
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
# All of the above gates are invoked directly by the agent (per the prose
# instructions in ``role_banner`` below) and by ``skills.research_gates``
# (``render_active_repair_blocks`` scans on-disk ``*_GATE_STATE.json``); none of
# them is wired through a shell-command registry.

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
        import json as _json

        p = Path(str(project_root)) / "research" / "PIPELINE_STATE.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        return str(data.get("current_stage", "") or "")
    except Exception:  # noqa: BLE001
        return ""


# Stage-ENTRY contracts (gate-forward): the required artifacts, minimum standard,
# claim constraints, and forbidden overclaims are stated BEFORE the stage runs — so
# the agent builds to the gate standard from the start instead of running-then-repairing.
_STAGE_ENTRY_CONTRACTS: dict[str, str] = {
    "scope": (
        "## STAGE-ENTRY CONTRACT — scope (build to this BEFORE finishing the stage)\n"
        "- REQUIRED ARTIFACT: PRIOR_WORK_MATRIX.csv — >= 8 direct prior works, >= 6 read FULL-TEXT "
        "(honest fulltext_status), per-paper overlap/difference/claim-implication.\n"
        "- MIN STANDARD: every headline claim maps to a closest_prior_work.\n"
        "- CLAIM CONSTRAINT: no original-research-article framing until the Literature gate passes.\n"
        "- FORBIDDEN: citing metadata-only as full text; asserting novelty without a prior-work map.\n"
    ),
    "model": (
        "## STAGE-ENTRY CONTRACT — model\n"
        "- REQUIRED ARTIFACTS: DOMAIN_CLASSIFICATION.json + THEORY_OPPORTUNITY_AUDIT.csv "
        "(which theory capabilities apply, which were used, at what tier, with evidence).\n"
        "- MIN STANDARD: variables/units/equations explicit; approximation validity ranges stated.\n"
        "- CLAIM CONSTRAINT: a claim depending on a theory capability you did NOT execute must be downgraded.\n"
        "- FORBIDDEN: applying an approximation (e.g. RWA, continuum, mean-field) without a stated validity check.\n"
    ),
    "execute": (
        "## STAGE-ENTRY CONTRACT — execute\n"
        "- REQUIRED ARTIFACTS: NUMERICAL_STUDY_PLAN.csv + reproducible scripts/ + generated data/ with provenance.\n"
        "- MIN STANDARD: convergence/finite-size/scan checks proportional to each claim; each result has an evidence_file.\n"
        "- CLAIM CONSTRAINT: 'robust'/'protected' needs an executed robustness study; 'universal'/'phase diagram' needs an executed scan — else downgrade.\n"
        "- FORBIDDEN: presenting finite/toy numerics as a universal or infinite-system proof; missing-condition work that pretends to finish.\n"
    ),
    "review": (
        "## STAGE-ENTRY CONTRACT — review\n"
        "- REQUIRED ARTIFACTS: NOVELTY_CLAIM_TABLE.csv (closest prior work, already-known vs what-is-new, significance, calibrated wording) + PAPER_TYPE_CLASSIFIER.json.\n"
        "- MIN STANDARD: every final claim labeled supported/partial/inconclusive/unknown with its boundary.\n"
        "- CLAIM CONSTRAINT: paper type must be consistent with the upstream gates; original-article ONLY if Literature AND Novelty gates pass.\n"
        "- FORBIDDEN: original framing without genuine, prior-work-separated novelty.\n"
    ),
    "manuscript": (
        "## STAGE-ENTRY CONTRACT — manuscript\n"
        "- REQUIRED: MANUSCRIPT.{md,tex,pdf} + SUPPLEMENT.{tex,pdf} + PAPER_BUILD_LOG.md, honoring PAPER_TYPE_CLASSIFIER.json and NOVELTY_CLAIM_TABLE allowed_wording.\n"
        "- MIN STANDARD: ONE central thesis + one stated non-trivial physical insight; boundaries/disclaimers stated at most TWICE total, in Results/Limitations — NOT repeated in every section.\n"
        "- CLAIM CONSTRAINT: spend the space on the PHYSICAL MEANING of what WAS done, not on lists of what was not done.\n"
        "- FORBIDDEN: over-hedging (repeating 'not a new phase / not universal / no disorder / no materials / no interactions / not a new bulk-edge theorem' across many sections); finite->universal or synthetic->real leaps.\n"
    ),
}


def stage_entry_contract(stage: str) -> str:
    """Return the stage-entry contract text for ``stage`` (empty if none)."""
    return _STAGE_ENTRY_CONTRACTS.get((stage or "").strip().lower(), "")


def _mode_banner(project_root: object = None) -> str:
    """Tiered run-mode notice for the active innovation tier.

    Replaces the old single Nature/Science original-research notice. The reviewer must
    evaluate against the ACTIVE tier (from TIER_STATE.json / START_TIER), downgrade is a
    change of claim TYPE (not a rigor cut). Original-research-required mode (if the
    operator opted in) is still honoured as a stretch."""
    try:
        from .downgrade import read_current_tier
        from .tiers import tier_rubric_banner

        tier = read_current_tier(project_root)
        block = "\n" + tier_rubric_banner(tier)
        block += (
            "## TIERED RESEARCH MODE\n"
            "The innovation gate is TIERED (S/A/B/C/D), default target Tier B. If the current tier is "
            "not supported after the allowed effort (pivot / model<->execute caps, repeated blockers, "
            "or an existing closure artifact), the Auto-Downgrade gate steps DOWN one rung (a change of "
            "claim TYPE, never a cut in rigor); the reviewer ratifies and then judges against the new "
            "tier only. "
        )
        block += "\n"
        # Original-research stretch notice (only when the operator opted in).
        try:
            from .mode_config import is_original_research_required

            if is_original_research_required():
                block += (
                    "## STRETCH — ORIGINAL RESEARCH REQUESTED\n"
                    "The operator set an original-research stretch target. Run the Novelty-Seeking Loop "
                    "(<=2 pivots); if insufficient, DOWNGRADE per the tier ladder to a bounded contribution "
                    "or preserve the Tier-D negative evidence and replan — do not livelock at the stretch tier.\n"
                )
        except Exception:  # noqa: BLE001
            pass
        return block
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
        try:
            from ...skills.research_gates import render_active_repair_blocks

            gblocks = render_active_repair_blocks(project_root)
            if gblocks:
                repair += "\n\n" + gblocks
        except Exception:  # noqa: BLE001 — research-gate repair context must never break the banner
            pass
        # Structured gate-fail feedback (role-addressed): who/what/how-checked/next-stage.
        try:
            from .gate_feedback import render_active_feedback

            fblocks = render_active_feedback(project_root)
            if fblocks:
                repair += "\n\n" + fblocks
        except Exception:  # noqa: BLE001 — gate-fail feedback must never break the banner
            pass
        # Context-compaction policy: refresh the digest + prepend the pointer directive.
        try:
            from .context_policy import context_policy_banner, write_digest

            write_digest(project_root)
            repair = "\n\n" + context_policy_banner() + repair
        except Exception:  # noqa: BLE001 — context policy must never break the banner
            pass
    # Gate-forward: prepend the CURRENT stage's entry contract + run-mode notice so the
    # agent builds to the gate standard from the start (not only after a failed exit check).
    entry = stage_entry_contract(_current_stage(project_root))
    if entry:
        repair = "\n\n" + entry + repair
    mode = _mode_banner(project_root)
    if mode:
        repair = "\n\n" + mode + repair
    common = (
        "MISSION TYPE: PHYSICS. Work on a real physical system via theory, "
        "simulation, data analysis, literature synthesis, or experiment design. "
        "This is NOT a metric-optimization mission. The pipeline has FIVE stages — "
        "scope -> model -> execute -> review -> manuscript — and the TERMINAL "
        "deliverable of a completed physics mission is a standard research-paper "
        "package delivered in THREE layers. "
        "(1) VERIFICATION SOURCE LAYER: MANUSCRIPT.md (Abstract/Summary, Introduction, "
        "Background, Model/Theory, Methods, Results, Discussion, Limitations, "
        "Conclusion, References, Data & Code Availability), >=6 numbered figures with "
        "formal legends, >=8 real references, a CLAIMS.csv evidence ledger (its header "
        "MUST be exactly claim_id,claim_text,claim_type,evidence_type,evidence_pointer,"
        "status,boundary,reviewer_notes — no synonyms; do not use 'claim' or "
        "'evidence'), REPRODUCIBILITY.md, METHODS_DETAIL.md, and REVIEW.md. "
        "(2) PAPER COMPOSITION LAYER: a LaTeX-compiled, journal-style paper — "
        "MANUSCRIPT.tex -> MANUSCRIPT.pdf and SUPPLEMENT.tex -> SUPPLEMENT.pdf, plus a "
        "PAPER_BUILD_LOG.md. The default layout profile is physics_two_column_article "
        "(a two-column, article-based layout — 'revtex-like' means two columns, NOT a "
        "revtex dependency); use broad_science_review_draft (single-column, 12pt, "
        "double-spaced) only when the task explicitly asks for a Nature/Science "
        "initial-submission style. The paper needs >=4 numbered LaTeX display equations "
        "(each \\label'd, with >=3 in-text 'Eq. (n)' references), >=2 main tables and "
        ">=2 supplementary tables, every numbered figure placed near its discussion and "
        "cited as 'Fig. N', and a real References section (12-30 references for a formal "
        "run). "
        "(3) OPTIONAL PRESENTATION LAYER: HTML_DEMO/index.html or PRESENTATION/index.html "
        "for a manager view — this layer NEVER gates and is not required. "
        "PAPER-LANGUAGE POLISH: the paper main text must read as scientific prose, not an "
        "engineering report. Keep numbered citations in ONE consistent style ([n] or "
        "superscript, resolved via \\cite), and DISTRIBUTE them: >= 12 in-text citations "
        "spread through the core sections — every substantive subsection (>= 60 words) and "
        "every one-to-two substantive paragraphs carry a citation; do not pile them in the "
        "Introduction. Cite the source for each mechanism, model/method choice, and "
        "comparison with prior work; a Results subsection reporting only this study's own "
        "numerics may cite a Fig./Table instead. The main text must NOT contain the tokens "
        "artifact, verifier, stage_check, project_done, Argus, workspace, 'generated by', "
        "'source table', CLAIMS.csv, REVIEW.md, METHODS_DETAIL.md, REPRODUCIBILITY.md; the "
        "path/extension tokens scripts/, data/, .json, .csv are allowed ONLY inside the "
        "Data/Code availability statement or the Supplement. Data & Code availability use "
        "plain language with no absolute paths and no long command blocks (commands, file "
        "names, and hashes belong in the Supplement). REVIEW.md must contain a section "
        "titled exactly '## " + PAPER_AUDIT_HEADING + "' (this heading lives in REVIEW.md "
        "only, never in the paper). No finite-numerics->universal and no synthetic->real "
        "overclaim.\n"
        "RESEARCH RAW MATERIALS (produced at the stages, not improvised at the end): at the "
        "SCOPE stage, actually read and position the closest DIRECT prior work — build "
        "PRIOR_WORK_MATRIX.csv (>= 8 direct prior works, >= 6 read full-text, honest "
        "fulltext_status, per-paper overlap/difference/special-features and claim implication) "
        "and run the Literature Positioning gate "
        "(`python -m argus_skill.verticals.physics.gates.literature check --project-root .`). "
        "This gate is ADVISORY (it does not block scope->model), but you MUST resolve every "
        "failure_id it reports before relying on the literature positioning: read "
        "research/LITERATURE_GATE_REPAIR_TASKS.md and fix each item. If the literature gate has "
        "NOT passed, the paper may NOT be framed as an original research article — claims that "
        "lack a mapped closest prior work must be downgraded (partial/inconclusive) or moved to "
        "Limitations, and the review must record the gap. "
        "At the MODEL stage, classify the domain (DOMAIN_CLASSIFICATION.json) and audit which "
        "theoretical capabilities apply, which you used and at what depth "
        "(THEORY_OPPORTUNITY_AUDIT.csv), then run the Theory Capability gate "
        "(`python -m argus_skill.verticals.physics.gates.theory check --project-root .`). "
        "This gate is ADVISORY; resolve every failure_id, and if a theory capability a claim "
        "depends on is missing, either execute it or downgrade the claim. "
        "At the EXECUTE stage, plan and evidence a numerical study proportional to the claims "
        "(NUMERICAL_STUDY_PLAN.csv) and run the Numerical Capability gate "
        "(`python -m argus_skill.verticals.physics.gates.numerical check --project-root .`). "
        "This gate is ADVISORY, but a robust/protected claim needs a used+evidenced robustness "
        "study and a phase-diagram/universal claim needs a used+evidenced parameter scan — "
        "otherwise downgrade the claim. "
        "At the REVIEW stage, audit novelty per claim (NOVELTY_CLAIM_TABLE.csv: closest prior "
        "work, known vs new, significance, calibrated wording) and classify the result type "
        "(PAPER_TYPE_CLASSIFIER.json), then run the Novelty and Paper-Type gates "
        "(`python -m argus_skill.verticals.physics.gates.novelty check --project-root .` and "
        "`... gates.paper_type check --project-root .`). These are ADVISORY, but the paper "
        "type must be consistent with the upstream gates: a paper may be an original research "
        "article candidate ONLY if the Literature and Novelty gates pass; otherwise use a lower "
        "type (benchmark / reproduction / training report) and the calibrated allowed_wording.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Drive physics-specific route selection from the actual physical "
            "structure of the task: decide whether it needs theoretical derivation, "
            "numerical simulation, data analysis, literature synthesis, experiment "
            "design, or a bounded negative result. There is no fixed paper pipeline here; do "
            "not force a fixed sequence of stages onto the problem. Before execute, "
            "require that the physical system, its domain, the observables, the "
            "assumptions, and the success criteria are explicit. Reuse "
            "reviewer-certified prior-stage evidence by precise reference; do not "
            "assign another full-tree audit, snapshot, manifest, or checksum without "
            "a concrete new dependency that requires it."
        ) + repair
    if role_norm == "engineer":
        return common + (
            "Dynamically choose the path that fits this task — derivation, "
            "simulation, data analysis, literature synthesis, experiment design, or "
            "a bounded negative result — instead of mechanically running a fixed workflow. "
            "Make the variables, equations, units, assumptions, and boundary/initial "
            "conditions explicit, and state the evidence limits of every result. In "
            "the relevant tasks report residual, convergence, uncertainty, and "
            "provenance. Do not treat a toy demo, metadata, or a workflow artifact "
            "as physical success; when a key condition (data, apparatus, or "
            "full-text literature) is missing, return an explicit blocker or a clearly "
            "bounded surrogate rather than pretending to finish."
        ) + repair
    if role_norm == "reviewer":
        return common + (
            "Independently audit physical-system fidelity, model validity, unit and "
            "dimensional consistency, boundary and initial conditions, numerical and "
            "data evidence, uncertainty, and the claim boundary. Check units and "
            "dimensions where they apply and check boundary and initial conditions "
            "where they apply. Reject agent-workflow and meta-paper drift, "
            "unsupported novelty, and fake success. Distinguish full-text, excerpt, "
            "code/data, metadata-only, and unavailable evidence, and never treat "
            "metadata-only as full text. Require every final claim to be labeled "
            "supported, partial, inconclusive, or unknown."
        ) + repair
    return common + repair


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "stage_entry_contract",
]
