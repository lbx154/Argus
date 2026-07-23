"""Dynamic-path vertical for computational and experimental chemistry.

Chemical literature and database work, cheminformatics, quantum chemistry,
simulation, model training, active learning, and authorized instrument-backed
experiments are methods selected for the chemical problem, not mandatory pipeline
stages. The three coarse stages preserve that freedom while requiring an
independently reviewed result.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("frame", "investigate", "review")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"
REQUIRE_INDEPENDENT_REVIEW = True

# Chemistry missions end through the ordinary Reviewer-certified final stage.
# A paper, numeric target, or physical experiment is required only when the task says so.
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    stage: [_PIPELINE_CHECK] for stage in STAGE_ORDER
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "frame": (
        "reviewer/chemistry-review.md",
        "Check that the chemical system, question, observables, success bar, "
        "evidence regime, available capabilities, and external action boundary are "
        "clear enough to support real work. Preserve the capability under test, "
        "including whether decisions are online or frozen. Do not require a "
        "planning artifact.",
        [],
    ),
    "investigate": (
        "reviewer/chemistry-review.md",
        "Review the actual chemical work and raw tool or instrument evidence. "
        "Methods are chosen from the problem; a proposal, metadata record, or "
        "unexecuted workflow is not a chemical result.",
        [],
    ),
    "review": (
        "reviewer/chemistry-review.md",
        "Independently decide whether the result answers the original scientific "
        "question, survives the relevant controls and baselines, is reproducible "
        "within its evidence regime, and is stated without overclaiming.",
        [],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "frame": (
        ChecklistItem(
            id="frame.question-system-observables",
            statement=(
                "The original chemistry question is represented faithfully, with "
                "the chemical system, composition, molecular or reaction regime, "
                "variables, structures, and observables explicit enough to investigate."
            ),
            evidence_hint=(
                "the task as understood, including the chemical system and the "
                "property, structure, reaction outcome, behavior, or claim to determine"
            ),
        ),
        ChecklistItem(
            id="frame.success-evidence-regime",
            statement=(
                "Success and the evidence regime are explicit: literature synthesis, "
                "database retrieval, predictive oracle, simulation, quantum calculation, "
                "retrospective assay data, or physical measurement are not treated as "
                "interchangeable. Agent benchmarks declare the capability under test, "
                "who makes each decision, its decision cadence, and what access the "
                "evaluator threat model actually prevents. An online-agent objective is "
                "not silently replaced by a policy frozen before outcomes."
            ),
            evidence_hint=(
                "a checkable success bar and an honest label for where chemical "
                "observations or oracle responses come from, plus the control path, "
                "decision cadence, freeze point, and evaluator-access boundary"
            ),
        ),
        ChecklistItem(
            id="frame.feasible-capabilities",
            statement=(
                "The route fits the chemistry tools, data, compute, permissions, and time that "
                "are actually available. Physical actions stay inside an externally "
                "authorized capability boundary with facility or instrument interlocks; "
                "missing access leads to a bounded surrogate or stop recommendation."
            ),
            evidence_hint=(
                "a real capability probe, source or endpoint check, and any applicable "
                "external authorization boundary"
            ),
        ),
    ),
    "investigate": (
        ChecklistItem(
            id="investigate.substantive-work",
            statement=(
                "Substantive chemical work was actually performed using methods "
                "appropriate to the question; an experiment plan, tool list, or "
                "unexecuted code path alone is not the result."
            ),
            evidence_hint=(
                "the calculation, query, analysis, simulation, model run, or authorized "
                "measurement and its direct output"
            ),
        ),
        ChecklistItem(
            id="investigate.input-method-fidelity",
            statement=(
                "Chemical inputs and method settings preserve the intended system. "
                "Identifiers, structures, stereochemistry, protonation or tautomer state, "
                "charge and spin, units, assay or reaction conditions, approximations, "
                "software versions, and convergence settings are checked where they matter."
            ),
            evidence_hint=(
                "the real chemical inputs and settings, plus structure, reaction, "
                "method, assay, or source checks appropriate to the claim"
            ),
        ),
        ChecklistItem(
            id="investigate.evaluation-controls",
            statement=(
                "Evaluation uses relevant chemistry controls, uncertainty checks, and "
                "the strongest appropriate domain baseline under a comparable budget. Held-out "
                "or hidden evaluation evidence is not leaked into proposal decisions. "
                "Policy provenance and evaluator isolation are stated precisely: an "
                "agent-designed fixed rule is not online agent control, and a same-user "
                "subprocess is not an adversarially sealed evaluator. When the requested "
                "claim concerns online agent decisions, a frozen policy does not satisfy it."
            ),
            evidence_hint=(
                "baseline and control results, uncertainty or repeat evidence, and the "
                "actual separation between proposal information and evaluation answers"
            ),
        ),
        ChecklistItem(
            id="investigate.adaptive-evidence",
            statement=(
                "When the chemistry task is sequential, later choices respond to observed "
                "results under the declared query or experiment budget. If online agent "
                "control is the capability under test, each decision is attributable to "
                "that live control path rather than precompiled policy code. Failures and "
                "negative results remain visible, and missing critical access is reported "
                "rather than replaced by invented or toy evidence."
            ),
            evidence_hint=(
                "the trajectory of proposals, decision provenance, and returned chemical "
                "observations, including failed calls or an honest bounded stop"
            ),
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.scientific-fidelity",
            statement=(
                "The result still concerns the original chemical system and observable, "
                "and the interpretation respects molecular or reaction identity, "
                "assumptions, domain of validity, units, structures, and conditions."
            ),
            evidence_hint="a direct comparison of the claim with the task and method inputs",
        ),
        ChecklistItem(
            id="review.execution-evidence",
            statement=(
                "The decisive evidence comes from inspectable primary outputs and a "
                "real execution or measurement. Metadata, a plausible narrative, or "
                "a model's own confidence is not substituted for the underlying result."
            ),
            evidence_hint=(
                "raw or minimally processed outputs tied to the tool, data source, "
                "computation, or instrument run that produced them"
            ),
        ),
        ChecklistItem(
            id="review.evaluation-integrity",
            statement=(
                "Baselines, controls, uncertainty, failure handling, and contamination "
                "risk are adequate for the claim. Simulation, proxy-oracle, retrospective "
                "data, and physical validation are distinguished explicitly. Labels such "
                "as agent-guided and sealed are no stronger than the recorded decision "
                "path, policy-freeze point, access controls, and threat model."
            ),
            evidence_hint=(
                "the comparison and validation evidence, including limitations of the "
                "chosen oracle or experimental regime"
            ),
        ),
        ChecklistItem(
            id="review.outcome-honest",
            statement=(
                "The conclusion says what was observed, computed, predicted, reproduced, "
                "improved, falsified, or left unresolved. Novelty is asserted only with "
                "an appropriate primary-source check. Honesty is required but is not "
                "scientific value by itself: a bounded negative result may complete its "
                "execution item, while review-stage completion requires a standalone "
                "decision-relevant finding or another valuable direction. Otherwise the "
                "Reviewer returns `redirect`/`stop` for replanning rather than repackaging "
                "failure as project success."
            ),
            evidence_hint="the final claim, its support, limitations, and sources if novelty matters",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Load chemistry context as a Skill for the generic role implementation."""
    role_name = (role or "").strip().lower()
    skill_name = {
        "manager": "manager/chemistry-manager.md",
        "planner": "planner/chemistry-planning.md",
        "engineer": "engineer/chemistry-execution.md",
        "reviewer": "reviewer/chemistry-review.md",
        "scientist_create": "scientist/chemistry-distillation.md",
        "scientist": "scientist/chemistry-adaptation.md",
    }.get(role_name)
    if skill_name is None:
        return ""
    text = (Path(__file__).parent / "skills" / skill_name).read_text(
        encoding="utf-8"
    )
    if text.startswith("---"):
        _frontmatter, _separator, body = text[3:].partition("---")
        return body.strip()
    return text.strip()


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REQUIRE_INDEPENDENT_REVIEW",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
]
