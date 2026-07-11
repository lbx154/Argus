"""Minimal dynamic-path vertical for mathematical research.

The stages are deliberately coarse. Background retrieval, examples and
counterexamples, computation, natural-language proof, and Lean formalization are
methods selected for the problem at hand, not mandatory pipeline stages.
"""
from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ("scope", "solve", "review")
CHECKLIST_STAGE_ORDER = STAGE_ORDER

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
        "reviewer/argus-reviewer-role.md",
        "Check that the original mathematical problem, objects, quantifiers, "
        "assumptions, problem type, success criterion, and problem-specific "
        "research route are explicit.",
        [],
    ),
    "solve": (
        "reviewer/argus-reviewer-role.md",
        "Check the mathematical evidence actually produced. Enforce the limits "
        "of counterexamples, finite computation, natural-language proof, and any "
        "claimed Lean compilation.",
        [],
    ),
    "review": (
        "reviewer/argus-reviewer-role.md",
        "Independently audit correctness and fidelity to the original problem. "
        "Reject hidden assumptions, weakened conclusions, uncompiled Lean, and "
        "overclaims about open or unresolved questions.",
        [],
    ),
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.problem-explicit",
            statement=(
                "The original mathematical problem is stated precisely: all objects, "
                "domains, quantifiers, definitions, and hypotheses are explicit."
            ),
            evidence_hint="a faithful problem statement with no implicit variables or assumptions",
        ),
        ChecklistItem(
            id="scope.success-criterion",
            statement=(
                "The problem type and success criterion are explicit: prove, disprove, "
                "construct, classify, estimate, or make bounded progress on an open problem."
            ),
            evidence_hint="a declared problem type and an honest criterion for completion",
        ),
        ChecklistItem(
            id="scope.dynamic-route",
            statement=(
                "The chosen research route matches this problem. Background retrieval, "
                "counterexample search, computation, proof construction, and Lean are "
                "selected only when useful rather than forced as a fixed pipeline."
            ),
            evidence_hint="a problem-specific route with reasons for included and skipped methods",
        ),
    ),
    "solve": (
        ChecklistItem(
            id="solve.checkable-evidence",
            statement=(
                "Every key mathematical conclusion has checkable supporting evidence: "
                "a complete argument, a valid construction or counterexample, or clearly "
                "bounded computational evidence."
            ),
            evidence_hint="explicit derivations, witnesses, checked cases, or reproducible outputs",
        ),
        ChecklistItem(
            id="solve.counterexample-valid",
            statement=(
                "Any claimed counterexample satisfies every original definition and "
                "hypothesis before violating the claimed conclusion."
            ),
            evidence_hint="premise-by-premise verification of each counterexample",
        ),
        ChecklistItem(
            id="solve.computation-bounded",
            statement=(
                "Finite computation or numerical experimentation is not presented as a "
                "proof of an infinite or universal statement."
            ),
            evidence_hint="the tested range and the precise evidentiary limit are stated",
        ),
        ChecklistItem(
            id="solve.lean-compiled",
            statement=(
                "When Lean is used, the submitted source has fresh successful compilation "
                "evidence and contains no `sorry`, `admit`, or equivalent proof hole."
            ),
            evidence_hint="Lean source plus compiler command, version, exit status, and output",
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.statement-fidelity",
            statement=(
                "The natural-language problem and every formal statement are faithfully "
                "equivalent in objects, quantifiers, hypotheses, and conclusion."
            ),
            evidence_hint="side-by-side audit of the original and formalized statements",
        ),
        ChecklistItem(
            id="review.no-goal-drift",
            statement=(
                "The solution did not silently add assumptions, restrict the domain, "
                "weaken the conclusion, or replace the original problem with an easier one."
            ),
            evidence_hint="an explicit premise-and-conclusion comparison",
        ),
        ChecklistItem(
            id="review.lean-not-sufficient",
            statement=(
                "Lean compilation is treated only as evidence about the encoded theorem; "
                "it is not treated as sufficient evidence that the encoding faithfully "
                "represents the original problem."
            ),
            evidence_hint="independent semantic review of definitions and theorem statement",
        ),
        ChecklistItem(
            id="review.open-problem-honesty",
            statement=(
                "For an open or unresolved problem, the conclusion distinguishes proved "
                "results, disproofs, experiments, conjectures, partial progress, and unknowns "
                "without exaggeration."
            ),
            evidence_hint="claim-by-claim status labels and stated remaining gaps",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Frame each role around dynamic mathematical research and honest evidence."""
    common = (
        "MISSION TYPE: MATHEMATICS. Work on a conjecture, proof, construction, "
        "counterexample, or open research problem. This is NOT a paper pipeline "
        "and NOT a metric-optimization mission.\n"
    )
    role_norm = (role or "").strip().lower()
    if role_norm == "planner":
        return common + (
            "Choose work from the actual mathematical structure of the problem. "
            "Use background retrieval, examples, counterexamples, computation, "
            "proof construction, or Lean only when they are useful; do not turn "
            "the method menu into a fixed pipeline."
        )
    if role_norm == "engineer":
        return common + (
            "Dynamically choose the path that fits this problem; do not mechanically "
            "execute a fixed workflow. Clearly distinguish conjecture, finite or "
            "numerical evidence, natural-language proof, and formal verification. "
            "State the limits of every result and compile Lean claims for real."
        )
    if role_norm == "reviewer":
        return common + (
            "Independently check mathematical correctness and fidelity to the original "
            "definitions, quantifiers, assumptions, and conclusion. Check the boundary "
            "of computational evidence. If Lean is used, verify fresh real compilation "
            "and reject proof holes. Lean compilation does not prove that the formal "
            "statement faithfully represents the original problem."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
]
