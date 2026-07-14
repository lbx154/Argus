"""Dynamic-path vertical for physics tasks with a research-paper terminal stage.

The five stages are deliberately coarse. Theoretical derivation, numerical
simulation, data analysis, literature synthesis, and experiment design are
*methods* selected for the physical task at hand, not mandatory pipeline
stages. This is a lightweight, Argus-native physics vertical — not a large
physics research framework: it ships the stage contract, role framing, and
reviewer checklists, and leaves heavy checker/solver/literature machinery out.

Stage semantics:

* ``scope``   — pin down the original physics task: system, domain, observables,
  task type, success criterion, and a feasible route (or an honest no-go).
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

from ...skills.stage_checklists import ChecklistItem
from .manuscript import PAPER_AUDIT_HEADING, manuscript_review_items

STAGE_ORDER = ("scope", "model", "execute", "review", "manuscript")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"

# Physics missions end through the ordinary reviewer-certified final-stage path.
# They are neither paper-submission missions nor metric-optimization campaigns.
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

# ``manuscript`` is the mandatory terminal stage: a completed physics mission's
# deliverable is a standard research-paper package, not a scope/model/execute/
# review log. The shell check runs the always-fail-closed manuscript verifier in
# ``manuscript.py`` (no optional mode, no marker file, no env var). ``{python}``
# is substituted with the checker's interpreter by ``argus_skill.tools.stage_check``.
_MANUSCRIPT_CHECK = (
    "Research-paper delivery contract satisfied (terminal manuscript stage)",
    "{python} -m argus_skill.verticals.physics.manuscript check --project-root .",
)

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    stage: [_PIPELINE_CHECK] for stage in STAGE_ORDER
}
STAGE_CHECKS["manuscript"] = [_PIPELINE_CHECK, _MANUSCRIPT_CHECK]

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "scope": (
        "reviewer/argus-reviewer-role.md",
        "Check that the original physics task is stated faithfully: the physical "
        "system, domain, and observables are explicit; the task type and success "
        "criterion are explicit; and a feasible route (theory, simulation, data "
        "analysis, literature synthesis, experiment design, or an honest no-go) "
        "is chosen from the real structure of the problem.",
        [],
    ),
    "model": (
        "reviewer/argus-reviewer-role.md",
        "Check the model and evidence preparation: variables, parameters, units, "
        "equations, model or data sources, assumptions, approximation validity "
        "range, and boundary/initial conditions are explicit, and the observables "
        "and validation target (analytic limit, baseline, residual, convergence, "
        "uncertainty, or evidence boundary) are declared.",
        [],
    ),
    "execute": (
        "reviewer/argus-reviewer-role.md",
        "Check the physics evidence actually produced. Numerical, data, and "
        "literature claims must carry provenance; finite simulation or toy data "
        "must not be overclaimed as a universal conclusion; and a missing critical "
        "condition (data, apparatus, full-text literature) must yield an honest "
        "NO_GO or a clearly bounded surrogate, not a pretended completion.",
        [],
    ),
    "review": (
        "reviewer/argus-reviewer-role.md",
        "Independently audit physical-system fidelity and claim boundaries. Reject "
        "physical-system drift, agent-workflow / meta-paper drift, and "
        "toy-overclaim; check units/dimensions and boundary/initial conditions "
        "where they apply; require an explicit numerical/data/literature evidence "
        "boundary; never accept metadata-only as full text; and require each final "
        "claim to be labeled supported, partial, no-go, inconclusive, or unknown.",
        [],
    ),
    "manuscript": (
        "reviewer/argus-reviewer-role.md",
        manuscript_review_items(),
        [],
    ),
}

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
                "literature synthesis, experiment design, or an honest no-go — not a "
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
            id="execute.honest-nogo",
            statement=(
                "When a critical condition is missing (data, apparatus, or full-text "
                "literature), the work returns an honest NO_GO or a clearly bounded "
                "surrogate instead of pretending to be complete."
            ),
            evidence_hint="an explicit NO_GO / bounded-surrogate note naming the missing condition",
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
                "Every final claim is labeled supported, partial, no-go, inconclusive, "
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
                "supported/partial/no-go/inconclusive/unknown status, and a boundary. "
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


def role_banner(role: str) -> str:
    """Frame each role around dynamic physics work and honest, bounded evidence."""
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
        "superscript, resolved via \\cite). The main text must NOT contain the tokens "
        "artifact, verifier, stage_check, project_done, Argus, workspace, 'generated by', "
        "'source table', CLAIMS.csv, REVIEW.md, METHODS_DETAIL.md, REPRODUCIBILITY.md; the "
        "path/extension tokens scripts/, data/, .json, .csv are allowed ONLY inside the "
        "Data/Code availability statement or the Supplement. Data & Code availability use "
        "plain language with no absolute paths and no long command blocks (commands, file "
        "names, and hashes belong in the Supplement). REVIEW.md must contain a section "
        "titled exactly '## " + PAPER_AUDIT_HEADING + "' (this heading lives in REVIEW.md "
        "only, never in the paper). No finite-numerics->universal and no synthetic->real "
        "overclaim.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Drive physics-specific route selection from the actual physical "
            "structure of the task: decide whether it needs theoretical derivation, "
            "numerical simulation, data analysis, literature synthesis, experiment "
            "design, or an honest no-go. There is no fixed paper pipeline here; do "
            "not force a fixed sequence of stages onto the problem. Before execute, "
            "require that the physical system, its domain, the observables, the "
            "assumptions, and the success criteria are explicit. Reuse "
            "reviewer-certified prior-stage evidence by precise reference; do not "
            "assign another full-tree audit, snapshot, manifest, or checksum without "
            "a concrete new dependency that requires it."
        )
    if role_norm == "engineer":
        return common + (
            "Dynamically choose the path that fits this task — derivation, "
            "simulation, data analysis, literature synthesis, experiment design, or "
            "an honest no-go — instead of mechanically running a fixed workflow. "
            "Make the variables, equations, units, assumptions, and boundary/initial "
            "conditions explicit, and state the evidence limits of every result. In "
            "the relevant tasks report residual, convergence, uncertainty, and "
            "provenance. Do not treat a toy demo, metadata, or a workflow artifact "
            "as physical success; when a key condition (data, apparatus, or "
            "full-text literature) is missing, return an honest NO_GO or a clearly "
            "bounded surrogate rather than pretending to finish."
        )
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
            "supported, partial, no-go, inconclusive, or unknown."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
]
