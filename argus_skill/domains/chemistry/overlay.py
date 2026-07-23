"""Chemistry additions to the research workflow contract."""

from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": (
        ChecklistItem(
            id="research.chemistry-system",
            statement=(
                "The chemical system is represented faithfully: composition, molecular "
                "or reaction identity, regime, structures, variables, and observables "
                "are explicit enough to investigate."
            ),
            evidence_hint=(
                "chemical identifiers and the property, structure, reaction outcome, "
                "behavior, or mechanism the project will determine"
            ),
        ),
        ChecklistItem(
            id="research.chemistry-evidence-regime",
            statement=(
                "Database retrieval, predictive oracle output, simulation, quantum "
                "calculation, retrospective assay data, and physical measurement are "
                "not treated as interchangeable evidence."
            ),
            evidence_hint=(
                "an explicit evidence ceiling and source for each claim-critical "
                "chemical observation"
            ),
        ),
    ),
    "plan": (
        ChecklistItem(
            id="plan.chemistry-capabilities",
            statement=(
                "The plan fits available chemistry tools, data, compute, licenses, "
                "permissions, and time. Missing instrument or service access leads to "
                "a clearly bounded surrogate or explicit blocker."
            ),
            evidence_hint=(
                "real package, source, endpoint, model-weight, license, and authorization "
                "checks for the selected route"
            ),
        ),
        ChecklistItem(
            id="plan.chemistry-control-provenance",
            statement=(
                "Agent experiments define the capability under test, decision owner, "
                "decision cadence, and policy-freeze point. An online-agent objective "
                "is not silently replaced by a policy frozen before outcomes."
            ),
            evidence_hint=(
                "the live, periodic, frozen, or conventional control path and how each "
                "budgeted decision will be attributed"
            ),
        ),
    ),
    "benchmark": (
        ChecklistItem(
            id="benchmark.chemistry-input-fidelity",
            statement=(
                "Chemical inputs preserve identifiers, structures, stereochemistry, "
                "protonation or tautomer state, charge and spin, units, conditions, "
                "approximations, software versions, and convergence settings where relevant."
            ),
            evidence_hint=(
                "structure, reaction, method, assay, canonicalization, or source checks "
                "appropriate to the benchmark claim"
            ),
        ),
        ChecklistItem(
            id="benchmark.chemistry-evaluator-boundary",
            statement=(
                "The evaluator and split match the chemical claim. Hidden labels or "
                "future observations do not reach proposal logic, and a same-user "
                "subprocess is not described as adversarial sealing."
            ),
            evidence_hint=(
                "evaluator provenance, access controls, split logic, contamination "
                "analysis, and the stated cooperative or adversarial threat model"
            ),
        ),
    ),
    "run": (
        ChecklistItem(
            id="run.chemistry-primary-evidence",
            statement=(
                "Runs retain inspectable chemical inputs, primary outputs, failed calls, "
                "negative observations, seeds, versions, conditions, and decision traces."
            ),
            evidence_hint=(
                "raw or minimally processed tool, oracle, simulation, calculation, or "
                "instrument records tied to each reported run"
            ),
        ),
        ChecklistItem(
            id="run.chemistry-online-control",
            statement=(
                "When online agent control is the tested capability, every budgeted "
                "decision is attributable to the live agent and its observed history; "
                "precompiled policy code does not satisfy this item."
            ),
            evidence_hint=(
                "per-decision agent context, action, returned observation, and budget index"
            ),
        ),
    ),
    "analysis": (
        ChecklistItem(
            id="analysis.chemistry-interpretation",
            statement=(
                "Analysis respects chemical identity, assumptions, domain of validity, "
                "units, conditions, oracle/model bias, controls, uncertainty, and the "
                "difference between predicted and measured effects."
            ),
            evidence_hint=(
                "claim-to-result analysis with chemistry-specific limitations and "
                "appropriate strong baselines"
            ),
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.chemistry-claim-integrity",
            statement=(
                "The final claim states what was retrieved, predicted, computed, "
                "simulated, or physically measured. Agent-guided and sealed labels are "
                "no stronger than the recorded control path and access boundary. An "
                "bounded negative result closes its experiment but does not by itself "
                "satisfy the research objective; unsupported or low-value routes return "
                "to replanning."
            ),
            evidence_hint=(
                "the paper claims, primary outputs, policy provenance, evaluator threat "
                "model, and chemical evidence ceiling"
            ),
        ),
    ),
}


def role_banner(role: str) -> str:
    """Load concise chemistry context for one persistent role."""
    name = {
        "manager": "manager/chemistry-manager.md",
        "planner": "planner/chemistry-planning.md",
        "engineer": "engineer/chemistry-execution.md",
        "reviewer": "reviewer/chemistry-review.md",
        "scientist_create": "scientist/chemistry-distillation.md",
        "scientist": "scientist/chemistry-adaptation.md",
    }.get(str(role or "").strip().lower())
    if name is None:
        return ""
    text = (Path(__file__).parent / "skills" / name).read_text(encoding="utf-8")
    if text.startswith("---"):
        _frontmatter, _separator, body = text[3:].partition("---")
        return body.strip()
    return text.strip()


__all__ = ["CHECKLIST_ITEMS", "role_banner"]
