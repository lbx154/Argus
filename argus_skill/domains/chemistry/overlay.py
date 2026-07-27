"""Chemistry additions to the research workflow contract."""

from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": (
        ChecklistItem(
            id="research.chemistry-system",
            statement=(
                "The chemical object, sample, material, framework, cell, crystal, "
                "reaction, or biological construct and the decision-relevant "
                "observable are identifiable with conditions and unresolved "
                "ambiguities."
            ),
            evidence_hint=(
                "source identifiers or files, identity assumptions, sample or "
                "construct state, units, conditions, and the scientific question"
            ),
        ),
        ChecklistItem(
            id="research.chemistry-evidence-regime",
            statement=(
                "Retrieved, curated, predicted, computed, simulated, measured, and "
                "inferred evidence are distinguished, with primary-source provenance "
                "and a claim ceiling appropriate to the objective."
            ),
            evidence_hint=(
                "claim-to-source mapping, original data or literature location, "
                "evidence labels, conflicts, and the maximum defensible conclusion"
            ),
        ),
    ),
    "plan": (
        ChecklistItem(
            id="plan.chemistry-capabilities",
            statement=(
                "The next action uses the narrowest matched chemistry workflow and "
                "available project capability, with required inputs, a representative "
                "probe, decisive acceptance check, and explicit stop conditions; "
                "explicit Playground work remains bounded and project-local."
            ),
            evidence_hint=(
                "matched domain Skill, input inventory, tool or data availability, "
                "capability probe, output contract, and blocker conditions"
            ),
        ),
        ChecklistItem(
            id="plan.chemistry-control-provenance",
            statement=(
                "Controls, baselines, grouping or split logic, uncertainty, physical "
                "authorization, and safety boundaries are defined before claim-critical "
                "outcomes are exposed."
            ),
            evidence_hint=(
                "control and baseline design, leakage boundary, uncertainty plan, "
                "authorized action scope, and facility or instrument constraints"
            ),
        ),
    ),
    "benchmark": (
        ChecklistItem(
            id="benchmark.chemistry-input-fidelity",
            statement=(
                "Benchmark inputs preserve chemically meaningful identity, structure, "
                "composition, stereochemistry, state, units, conditions, processing, "
                "protocol, and transformations required by the claim."
            ),
            evidence_hint=(
                "original and normalized inputs, transformation mapping, data "
                "dictionary, sample grouping, and identity validation"
            ),
        ),
        ChecklistItem(
            id="benchmark.chemistry-evaluator-boundary",
            statement=(
                "The evaluator measures the stated chemistry capability with related "
                "entities, future observations, duplicate sources, and hidden answers "
                "excluded from proposal or training logic."
            ),
            evidence_hint=(
                "oracle definition, split and grouping rules, duplicate/leakage audit, "
                "information available at each decision, and comparable baseline"
            ),
        ),
    ),
    "run": (
        ChecklistItem(
            id="run.chemistry-primary-evidence",
            statement=(
                "Execution retains original and prepared inputs, exact settings, "
                "versions, primary outputs, warnings, convergence or calibration "
                "diagnostics, controls, failures, and negative observations."
            ),
            evidence_hint=(
                "project-native source data, tool or instrument outputs, configuration, "
                "processing lineage, validation diagnostics, and failed cases"
            ),
        ),
        ChecklistItem(
            id="run.chemistry-online-control",
            statement=(
                "Adaptive or agent-guided work records the information, decision owner, "
                "action, returned observation, budget, and policy-freeze point; physical "
                "actions remain inside pre-authorized interlocks."
            ),
            evidence_hint=(
                "per-decision trajectory or a clear non-adaptive label, budget use, "
                "returned evidence, authorization record, and abort behavior"
            ),
        ),
    ),
    "analysis": (
        ChecklistItem(
            id="analysis.chemistry-interpretation",
            statement=(
                "Analysis tests controls, residuals, convergence, calibration, "
                "uncertainty, applicability, alternative explanations, and domain "
                "validation before translating evidence into chemical conclusions."
            ),
            evidence_hint=(
                "replicate or sensitivity analysis, residuals, competing models or "
                "assignments, uncertainty, limitations, and claim-to-evidence mapping"
            ),
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.chemistry-claim-integrity",
            statement=(
                "Independent review applies the relevant specialized chemistry rubric "
                "and keeps every claim at or below its retrieved, predicted, computed, "
                "simulated, measured, or inferred evidence level; Playground status "
                "never advances the formal Research stage."
            ),
            evidence_hint=(
                "specialized Reviewer verdict, original evidence, unresolved failures, "
                "authorization boundary, and the narrowest supported claim"
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
