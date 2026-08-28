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
  a conventional paper. The stage's only deterministic outcome check is that
  ``MANUSCRIPT.tex`` produced a current ``MANUSCRIPT.pdf``; the independent
  Reviewer judges the paper's scientific quality from the deliverable itself.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("scope", "model", "execute", "review", "manuscript")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"

# Physics missions end through the ordinary reviewer-certified final-stage path.
# They are neither paper-submission missions nor metric-optimization campaigns.
completion_gate = "none"

# The stage machine calls ``stage_completion_issues`` before it can mark the
# final stage done. This checks only the compiled-paper outcome.

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
            id="manuscript.compiled-paper",
            statement=(
                "The terminal deliverable is a conventional research paper, not a "
                "scope/model/execute/review log, and MANUSCRIPT.tex compiles to a "
                "current MANUSCRIPT.pdf."
            ),
            evidence_hint="a current compiled MANUSCRIPT.pdf and its MANUSCRIPT.tex source",
        ),
        ChecklistItem(
            id="manuscript.scientific-quality",
            statement=(
                "The paper communicates the physical result with evidence, citations, "
                "methods, figures or tables where they help, and explicit limitations "
                "in proportion to the claims it makes."
            ),
            evidence_hint="Reviewer assessment of the paper's claim-to-evidence fit and reproducibility",
        ),
        ChecklistItem(
            id="manuscript.no-overclaim",
            statement=(
                "No claim over-extends its evidence: finite numerics are not universal "
                "proofs, synthetic/toy results are not real-system validation, and "
                "novelty/discovery claims are bound to evidence or downgraded."
            ),
            evidence_hint="an evidence-bounded claim set with no finite->universal or synthetic->real leap",
        ),
    ),
}


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
    from .manuscript import verify_compiled_manuscript

    return tuple(verify_compiled_manuscript(project_root))


def role_banner(role: str, project_root: object = None) -> str:
    """Frame each role around dynamic physics work and honest evidence."""
    del project_root
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
        )
    if role_norm == "engineer":
        return common + (
            "Execute the most informative physical route, then follow the feedback. "
            "Make equations, units, assumptions, and boundary/initial conditions "
            "explicit. Report residuals, convergence, uncertainty, provenance, and "
            "evidence limits when they matter to the claim. A toy, metadata record, "
            "or workflow artifact is not physical evidence."
        )
    if role_norm == "reviewer":
        return common + (
            "Independently judge physical-system fidelity, dimensional consistency, "
            "boundary/initial conditions, numerical or data evidence, uncertainty, "
            "novelty, significance, and claim boundaries. Reject fabricated, "
            "metadata-only, or agent-workflow evidence, but do not demand fixed "
            "tables, counts, or certificates. Ask for replan when the idea is weak."
        )
    return common


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
