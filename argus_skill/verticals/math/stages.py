"""Minimal dynamic-path vertical for mathematical research.

The stages are deliberately coarse. Background retrieval, examples and
counterexamples, computation, natural-language proof, and Lean formalization are
methods selected for the problem at hand, not mandatory pipeline stages.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_checklists import ChecklistItem

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
        "Check that the original mathematical problem, objects, quantifiers, "
        "assumptions, problem type, success criterion, and problem-specific "
        "research route are explicit.",
        [],
    ),
    "solve": (
        "reviewer/math-research-review.md",
        "Check the mathematical evidence actually produced. Enforce the limits "
        "of counterexamples, finite computation, natural-language proof, and any "
        "claimed Lean compilation.",
        [],
    ),
    "review": (
        "reviewer/math-research-review.md",
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
                "evidence, contains no `sorry`, `admit`, or equivalent proof hole, and "
                "the canonical artifacts `Main.lean`, `compile.log`, `lean_check.json`, "
                "and `statement_fidelity.md` are present."
            ),
            evidence_hint=(
                "the four canonical Lean artifacts, including exact commands, versions, "
                "exit status, axiom audit, and a side-by-side statement audit"
            ),
        ),
        ChecklistItem(
            id="solve.result-classified",
            statement=(
                "Each material result is classified as known, finite verification, "
                "counterexample, partial progress, new candidate, novelty-unverified, "
                "or verified new result; its mathematical scope is not overstated."
            ),
            evidence_hint="a claim ledger with result class, correctness, novelty, and limitations",
        ),
        ChecklistItem(
            id="solve.mechanism-overlap-audit",
            statement=(
                "When solve work introduces or materially refines a theorem, operator, "
                "proof mechanism, obstruction certificate, or asymptotic route that may "
                "be advanced as new, a separate bounded theorem-overlap audit is completed "
                "before treating it as novelty-bearing or advancing it toward review. The "
                "audit checks the closest recent primary sources, their citation chains, "
                "and foundational adjacent-field terminology. If no such mechanism emerged, "
                "the claim ledger explicitly records that this trigger did not fire."
            ),
            evidence_hint=(
                "research/MECHANISM_OVERLAP_AUDIT.md with trigger, exact queries, dated "
                "primary sources, backward/forward citations, overlap mapping, and remaining "
                "uncertainty; or an explicit not-triggered claim-ledger entry"
            ),
        ),
        ChecklistItem(
            id="solve.counterexample-guided-refinement",
            statement=(
                "When a new conjecture, intermediate lemma, asymptotic generalization, "
                "or strengthened bound is proposed, the cheapest decisive falsification "
                "test is run before investing in a full proof. Counterexamples refine the "
                "statement or assumptions rather than being discarded. If no new candidate "
                "statement was proposed, this trigger is explicitly not applicable."
            ),
            evidence_hint=(
                "a smallest-counterexample search, bounded exhaustive/symbolic test, or "
                "adversarial premise audit linked to the candidate and its refined form"
            ),
        ),
        ChecklistItem(
            id="solve.construction-admissibility",
            statement=(
                "For construction or existential-answer problems, candidate generation is "
                "separated from proof: bounded examples may suggest a canonical witness, "
                "but the final witness must satisfy an explicit admissible vocabulary/shape "
                "and must not circularly restate the target property. If the problem supplies "
                "the object and asks only for a proof, this trigger is not applicable."
            ),
            evidence_hint=(
                "enumeration trace, explicit conjectured witness, independent admissibility "
                "check, and proof that the admitted witness satisfies the original statement"
            ),
        ),
        ChecklistItem(
            id="solve.relational-premise-map",
            statement=(
                "When the argument depends on a nontrivial library or literature of prior "
                "lemmas, the proof records a compact dependency graph: retrieved premises, "
                "their exact role, the missing bridge lemmas, and which nodes are known versus "
                "new. Routine self-contained proofs may mark this trigger not applicable."
            ),
            evidence_hint=(
                "research/LEMMA_GRAPH.md or an equivalent claim-ledger dependency section "
                "with premise sources, edges, and unresolved bridge nodes"
            ),
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
        ChecklistItem(
            id="review.correctness-novelty-separated",
            statement=(
                "Mathematical correctness and research novelty have independent "
                "verdicts with concrete evidence; a correct known result is not "
                "presented as a new contribution."
            ),
            evidence_hint="separate correctness and novelty findings for the strongest claim",
        ),
        ChecklistItem(
            id="review.novelty-gate",
            statement=(
                "A result is called verified-new only after primary-source novelty "
                "review; finite verification, partial work, and unverified candidates "
                "remain non-terminal."
            ),
            evidence_hint="novelty audit or an explicit non-terminal classification",
        ),
        ChecklistItem(
            id="review.mechanism-overlap-debt",
            statement=(
                "Every strongest claim based on a newly introduced theorem, operator, proof "
                "mechanism, or certificate has a mechanism-level primary-source overlap audit. "
                "Without it, correctness may be certified for a bounded item, but novelty "
                "remains unverified and publishable or doctoral completion is blocked."
            ),
            evidence_hint=(
                "the mechanism-overlap audit linked to the strongest claim, or a bounded "
                "Reviewer verdict that preserves novelty-unverified status and routes the "
                "missing audit as the next DAG node"
            ),
        ),
        ChecklistItem(
            id="review.ai4m-verifier-separation",
            statement=(
                "Generated conjectures, witnesses, and formal statements are judged by a "
                "separate verifier appropriate to their type: falsification for candidates, "
                "admissibility plus proof for constructions, and semantic back-translation "
                "for formalization. Passing one verifier is not substituted for another."
            ),
            evidence_hint=(
                "separate candidate-generation and verification artifacts with explicit "
                "falsification, admissibility, statement-fidelity, or compiler results"
            ),
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
