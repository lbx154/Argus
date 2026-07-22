"""Minimal dynamic-path vertical for mathematical research.

The stages are deliberately coarse. Background retrieval, examples and
counterexamples, computation, natural-language proof, and Lean formalization are
methods selected for the problem at hand, not mandatory pipeline stages.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("scope", "solve", "review")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"
RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")

# Math missions end through the ordinary reviewer-certified final-stage path.
# They are neither paper-submission missions nor metric-optimization campaigns.
completion_gate = "none"

_PIPELINE_CHECK = (
    "Pipeline state present",
    "test -f research/PIPELINE_STATE.json",
)

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    stage: [_PIPELINE_CHECK] for stage in STAGE_ORDER
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "scope": (
        "reviewer/math-research-review.md",
        "Confirm what problem is being solved and what would count as success. "
        "Do not require a planning artifact.",
        [],
    ),
    "solve": (
        "reviewer/math-research-review.md",
        "Review the mathematical result itself and the argument or real computation "
        "supporting it. Do not grade the presence of process documents.",
        [],
    ),
    "review": (
        "reviewer/math-research-review.md",
        "Independently decide whether the result is correct, answers the original "
        "question, and is described without overclaiming.",
        [],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.problem-explicit",
            statement=(
                "The problem is understood precisely enough to work on: the relevant "
                "objects, assumptions, quantifiers, and requested conclusion are clear."
            ),
            evidence_hint="the problem statement as actually understood",
        ),
        ChecklistItem(
            id="scope.success-criterion",
            statement=(
                "It is clear whether success means a proof, counterexample, construction, "
                "classification, estimate, or honest progress on an open problem."
            ),
            evidence_hint="the requested outcome and completion bar",
        ),
    ),
    "solve": (
        ChecklistItem(
            id="solve.substantive-result",
            statement=(
                "There is a substantive result relevant to the problem, supported by an "
                "argument, a valid witness, or a reproducible computation as appropriate."
            ),
            evidence_hint="the result and the mathematics or real run supporting it",
        ),
        ChecklistItem(
            id="solve.witness-valid",
            statement=(
                "Any counterexample or constructed object satisfies the original conditions; "
                "it is not a circular restatement or an answer to an easier problem."
            ),
            evidence_hint="a direct check of the relevant conditions",
        ),
        ChecklistItem(
            id="solve.support-matches-claim",
            statement=(
                "The strength of the conclusion matches the support: finite computation is "
                "not called a universal proof, and formal compilation is not treated as "
                "evidence for a mistranslated statement."
            ),
            evidence_hint="the actual tested range or compiler run and the stated limitation",
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.statement-fidelity",
            statement=(
                "The natural-language problem and every formal statement are faithfully "
                "equivalent in objects, quantifiers, hypotheses, and conclusion."
            ),
            evidence_hint="a direct comparison with the original question",
        ),
        ChecklistItem(
            id="review.argument-correct",
            statement=(
                "The main argument is independently convincing: important steps are justified, "
                "dependencies are available, and no hidden assumption closes the gap."
            ),
            evidence_hint="the argument itself and any cited dependency",
        ),
        ChecklistItem(
            id="review.outcome-honest",
            statement=(
                "The conclusion says plainly what was proved, disproved, computed, conjectured, "
                "or left open. Novelty is claimed only when an appropriate source check supports "
                "it; otherwise uncertainty is stated without blocking a valid bounded result."
            ),
            evidence_hint="the stated conclusion, limitations, and sources if novelty is claimed",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Load Math context as a Skill for the generic role implementation."""
    role_name = (role or "").strip().lower()
    skill_name = {
        "manager": "manager/math-research-manager.md",
        "planner": "planner/math-research-planning.md",
        "engineer": "engineer/math-research-execution.md",
        "reviewer": "reviewer/math-research-review.md",
        "scientist_create": "scientist/math-research-distillation.md",
        "scientist": "scientist/math-research-adaptation.md",
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
    "REVIEWER_CHECKLISTS",
    "RESEARCH_TARGET_LEVELS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
]
