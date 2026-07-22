"""Dynamic materials-science and materials-processing research vertical.

The stages describe scientific commitments, not a fixed solver pipeline. The
Planner chooses the scale and methods required by the question; atomistic
simulation, CAD/CAE, process simulation, data analysis, and physical experiments
remain optional routes rather than mandatory boxes.
"""

from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("scope", "grounding", "model", "execute", "validate", "report")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"
RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")
REQUIRE_INDEPENDENT_REVIEW = True

# Materials missions finish through the ordinary Reviewer-certified final stage.
# A report may be a research result, process-design package, reproduction, or
# bounded no-go; it is not automatically a paper-submission or metric campaign.
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

# Shell checks stay structural. Scientific correctness, solver convergence, and
# evidence quality belong to the Reviewer checklists below.
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    stage: [_PIPELINE_CHECK] for stage in STAGE_ORDER
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "scope": (
        "reviewer/materials-research-review.md",
        "Confirm the actual material system, processing or service regime, length/time "
        "scale, observables, research question, and success criterion. The proposed route "
        "must fit the question rather than forcing atomistic simulation, FEM, or experiments. "
        "If physical work is proposed, require authorization and applicable safety controls "
        "before execution.",
        [],
    ),
    "grounding": (
        "reviewer/materials-research-review.md",
        "Check primary literature, public data, prior methods, strongest relevant baseline, "
        "tool and license availability, and the boundary between known results and the "
        "proposed contribution. Reject inaccessible or fabricated sources.",
        [],
    ),
    "model": (
        "reviewer/materials-simulation-signoff.md",
        "Audit material identity and state, units, governing model, parameter provenance, "
        "constitutive law or interatomic/electronic-structure settings, geometry, mesh, "
        "boundary/initial conditions, calibration split, and declared validity range.",
        [],
    ),
    "execute": (
        "reviewer/materials-simulation-signoff.md",
        "Inspect the real computation or experiment. Require native solver or instrument "
        "evidence, exact versions and inputs, retained failures, and honest treatment of "
        "missing licenses, hardware, samples, or instruments. For physical work, verify that "
        "execution remained inside the approved procedure and safety envelope.",
        [],
    ),
    "validate": (
        "reviewer/materials-validation-review.md",
        "Independently test physical and numerical validity using appropriate convergence, "
        "sensitivity, conservation, limiting-case, baseline, public benchmark, and "
        "experimental comparisons. Reject calibration leakage and solver-success claims.",
        [],
    ),
    "report": (
        "reviewer/materials-research-review.md",
        "Trace every conclusion to actual evidence, distinguish simulation from physical "
        "experiment, state uncertainty and applicability limits, and require a reproducible "
        "delivery proportional to the claimed result.",
        [],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.material-system",
            statement=(
                "The material composition, phase or microstructure, processing or service "
                "state, relevant environment, and observables are explicit."
            ),
            evidence_hint="the actual material system, state, environment, and measured outputs",
        ),
        ChecklistItem(
            id="scope.scale-regime",
            statement=(
                "The relevant spatial and temporal scales and physical regime are identified, "
                "so an electronic, atomistic, mesoscale, continuum, process, data-driven, or "
                "experimental route can be chosen for physical reasons."
            ),
            evidence_hint="a justified scale/regime choice and the phenomena it retains or omits",
        ),
        ChecklistItem(
            id="scope.success-criterion",
            statement=(
                "Success is checkable and matches the request: explanation, calibrated model, "
                "property prediction, validated process design, reproduced result, new finding, "
                "or an honest bounded no-go."
            ),
            evidence_hint="a task-specific observable, comparator, tolerance, or decision criterion",
        ),
        ChecklistItem(
            id="scope.physical-safety",
            statement=(
                "If the route includes physical synthesis, processing, testing, or instrument "
                "operation, the authorized personnel, approved procedure, hazards, interlocks, "
                "required controls, and waste handling are established before execution; "
                "simulation-only work marks this item not applicable."
            ),
            evidence_hint="applicable laboratory/process authorization and controls, or a simulation-only rationale",
        ),
    ),
    "grounding": (
        ChecklistItem(
            id="grounding.primary-sources",
            statement=(
                "Claims about prior materials, mechanisms, models, and measurements are grounded "
                "in resolvable primary sources or authoritative databases at the evidence level "
                "actually available."
            ),
            evidence_hint="primary papers, standards, database records, or public datasets read directly",
        ),
        ChecklistItem(
            id="grounding.baseline-gap",
            statement=(
                "The strongest relevant baseline and closest prior work are identified, and any "
                "novelty or improvement claim states the exact difference rather than relying on "
                "a generic literature gap."
            ),
            evidence_hint="a direct comparison with the strongest applicable prior result",
        ),
        ChecklistItem(
            id="grounding.data-tools-access",
            statement=(
                "Data provenance, licenses, material-property sources, solver availability, "
                "compute, and experimental access are checked before the plan depends on them."
            ),
            evidence_hint="real access evidence and explicit constraints for data, software, compute, and lab equipment",
        ),
    ),
    "model": (
        ChecklistItem(
            id="model.physics-and-units",
            statement=(
                "The governing physics, variables, equations, units, parameter sources, "
                "assumptions, and validity range are explicit and dimensionally consistent."
            ),
            evidence_hint="the implemented model and parameter values with units and provenance",
        ),
        ChecklistItem(
            id="model.material-state",
            statement=(
                "Material behavior reflects the relevant composition, phase, temperature, "
                "strain rate, texture, defects, microstructure, and processing history rather "
                "than an unjustified generic material card."
            ),
            evidence_hint="material-state inputs and the evidence supporting each important dependency",
        ),
        ChecklistItem(
            id="model.discretization-boundaries",
            statement=(
                "Geometry, periodic cell or CAD model, discretization, interactions or contact, "
                "boundary/initial conditions, loads, ensembles, and solver controls match the "
                "physical question."
            ),
            evidence_hint="actual model inputs plus an explanation of the chosen boundaries and discretization",
        ),
        ChecklistItem(
            id="model.calibration-validation-split",
            statement=(
                "Parameters fitted or selected from data are separated from validation evidence; "
                "the evaluation does not reuse its target as an input or tune on hidden answers."
            ),
            evidence_hint="a declared calibration/selection set and an independent validation set or oracle",
        ),
    ),
    "execute": (
        ChecklistItem(
            id="execute.real-run",
            statement=(
                "The claimed calculation or experiment actually ran. Generated scripts, input "
                "decks, GUI screenshots, or submitted jobs alone are not solver or instrument results."
            ),
            evidence_hint="native outputs, logs, job identity, timestamps, and parsed observables from a completed run",
        ),
        ChecklistItem(
            id="execute.reproducible-provenance",
            statement=(
                "Inputs, structures or CAD, material parameters, pseudopotentials or force fields, "
                "mesh, software and versions, seeds, hardware, commands, and output artifacts are "
                "preserved to the level needed to reproduce the claim."
            ),
            evidence_hint="the exact executable path, versioned inputs, run command, environment, and native outputs",
        ),
        ChecklistItem(
            id="execute.health-failures",
            statement=(
                "Solver or experiment health is inspected during execution; divergence, "
                "non-convergence, unstable dynamics, distorted elements, unphysical states, "
                "instrument faults, and failed attempts remain visible and are not relabeled as data."
            ),
            evidence_hint="diagnostics and retained failure evidence alongside accepted runs",
        ),
        ChecklistItem(
            id="execute.capability-boundary",
            statement=(
                "Unavailable licenses, software, hardware, samples, or instruments produce an "
                "explicit blocker or a clearly labeled surrogate, never a fabricated run."
            ),
            evidence_hint="real capability discovery and an honest boundary for anything not executed",
        ),
        ChecklistItem(
            id="execute.physical-safety-compliance",
            statement=(
                "Any physical experiment or processing run stayed within its authorization, "
                "approved procedure, equipment interlocks, and material-handling and disposal "
                "controls; non-physical work states that this is not applicable."
            ),
            evidence_hint="run-specific authorization and safety records, or a non-physical execution rationale",
        ),
    ),
    "validate": (
        ChecklistItem(
            id="validate.numerical-convergence",
            statement=(
                "Numerical claims are supported by convergence or stability checks appropriate "
                "to the method: mesh/time step/cell size, k-points/cutoff, sampling duration, "
                "optimizer tolerance, or another justified control."
            ),
            evidence_hint="fresh convergence or stability results over the parameters that can change the conclusion",
        ),
        ChecklistItem(
            id="validate.physical-consistency",
            statement=(
                "Units, conservation laws, symmetries, limiting cases, monotonic trends, bounds, "
                "and known physical behavior are checked where applicable."
            ),
            evidence_hint="independent physical sanity checks tied to the actual observables",
        ),
        ChecklistItem(
            id="validate.independent-reference",
            statement=(
                "Headline results are compared like-for-like against an independent analytic "
                "result, public benchmark, trusted implementation, published measurement, or "
                "new physical experiment under matching conditions."
            ),
            evidence_hint="raw comparison data with matched material state, geometry, conditions, and metric",
        ),
        ChecklistItem(
            id="validate.sensitivity-uncertainty",
            statement=(
                "Important parameter, model-form, stochastic, and measurement uncertainties are "
                "tested or bounded, and conclusions are no stronger than their robustness."
            ),
            evidence_hint="sensitivity or uncertainty results for the assumptions that could reverse the claim",
        ),
        ChecklistItem(
            id="validate.integrity",
            statement=(
                "The evaluation is independent of optimization and protected against leakage, "
                "hard-coded known outputs, edited references, selective run deletion, and "
                "comparison across mismatched hardware, conditions, or metrics."
            ),
            evidence_hint="a frozen evaluator or protocol plus complete raw attempts and like-for-like baselines",
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.claim-evidence",
            statement=(
                "Every main claim points to actual calculations, raw data, figures, tables, "
                "literature, or experimental records, and the evidence supports its stated scope."
            ),
            evidence_hint="claim-by-claim traceability to inspectable evidence",
        ),
        ChecklistItem(
            id="report.simulation-experiment-boundary",
            statement=(
                "Predicted, simulated, surrogate, and experimentally measured quantities are "
                "distinguished explicitly; a digital result is not described as physical validation."
            ),
            evidence_hint="clear labels for each evidence type and any missing real-world validation",
        ),
        ChecklistItem(
            id="report.reproducibility-limitations",
            statement=(
                "The delivered result records reproduction instructions, material and process "
                "conditions, software or instrument details, uncertainty, negative results, "
                "known limitations, and the domain where the conclusion applies."
            ),
            evidence_hint="a proportional report or paper plus runnable inputs and retained raw evidence",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Load the four materials role contracts from the vertical skill package."""
    skill_name = {
        "manager": "manager/materials-research-manager.md",
        "planner": "planner/materials-research-planning.md",
        "engineer": "engineer/materials-research-execution.md",
        "reviewer": "reviewer/materials-research-review.md",
    }.get((role or "").strip().lower())
    if skill_name is None:
        return ""
    text = (Path(__file__).parent / "skills" / skill_name).read_text(encoding="utf-8")
    if text.startswith("---"):
        _frontmatter, _separator, body = text[3:].partition("---")
        return body.strip()
    return text.strip()


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REQUIRE_INDEPENDENT_REVIEW",
    "RESEARCH_TARGET_LEVELS",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
]
