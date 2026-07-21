"""Physics innovation tiering (S/A/B/C/D) — the tier ladder + per-tier rubric.

Replaces the single Nature/Science-level original-research gate with a five-rung
ladder so the vertical targets the tier its evidence supports and DOWNGRADES
honestly (a change of *claim type*, never a cut in rigor). Grounded in the sourced
standards in ``_cockpit_v5/PHYSICS_INNOVATION_TIERING_WITH_WEB_SOURCES.md``:

* S — Nature/Science (broad significance)      — aspiration only, never default.
* A — top specialist (PRL / Nature Physics).
* B — solid PhD / strong specialist (Comms Physics) — the DEFAULT target.
* C — ordinary PhD / general specialist (Scientific Reports; "technically sound
      only, novelty not assessed").
* D — no-go / negative / null result (Registered-Report logic) — a SUCCESS terminal.

Pure data + helpers, env-driven, no hardcoded run behaviour. No Argus core here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

ENV_START_TIER = "ARGUS_SKILL_PHYSICS_START_TIER"
ENV_NOGO_TERMINAL = "ARGUS_SKILL_PHYSICS_NOGO_TERMINAL"

#: Highest -> lowest. Downgrade walks left->right; D is terminal.
TIER_ORDER: tuple[str, ...] = ("S", "A", "B", "C", "D")

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


@dataclass(frozen=True)
class TierSpec:
    tier: str
    name: str
    claim_types: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    numerical_requirements: tuple[str, ...]
    reviewer_gate: str
    manuscript_gate: str
    downgrade_from_when: str
    stop_chasing_higher_when: str
    operator_auth_required: bool
    auto_downgrade_threshold: str
    source_anchor: str = ""
    extras: dict = field(default_factory=dict)


TIERS: dict[str, TierSpec] = {
    "S": TierSpec(
        tier="S", name="Nature/Science level (aspiration only; never the default gate)",
        claim_types=("major conceptual breakthrough", "universal/general mechanism",
                     "result that changes a subfield's direction with interdisciplinary interest"),
        evidence_requirements=("multiple independent lines", "mechanism generalizing beyond the model",
                               "strong falsification survival", "cross-model/cross-scale robustness"),
        numerical_requirements=("multi-model, multi-scale", "held-out + adversarial controls",
                                "convergence to thermodynamic/irrational limit", "full provenance"),
        reviewer_gate="validity + importance + BROAD/interdisciplinary significance (all three)",
        manuscript_gate="full original-research package + strong significance narrative",
        downgrade_from_when="broad significance not demonstrable after the first full model->execute cycle",
        stop_chasing_higher_when="N/A (top)",
        operator_auth_required=True,
        auto_downgrade_threshold="S->A after 1 failed cycle to show breadth, or immediately if the claim is single-model",
        source_anchor="Nature editorial criteria; Science reviewer instructions",
    ),
    "A": TierSpec(
        tier="A", name="Top specialist (PRL / Nature Physics)",
        claim_types=("clear original mechanism or new diagnostic that substantially advances the field",
                     "a 'method win' with strong validation"),
        evidence_requirements=("preregistered positive diagnostic beating a reproduced baseline on held-out tests",
                               "mechanistic explanation", "robustness across >=2 regimes/models"),
        numerical_requirements=("baseline reproduction + new method beating it on held-out data",
                                ">=2 independent regimes", "ablations", "convergence evidence"),
        reviewer_gate="validity + importance-within-field + held-out method win",
        manuscript_gate="original-research article; explicit claims ledger; honest limitations",
        downgrade_from_when="method win not achieved after the allotted novelty pivots",
        stop_chasing_higher_when="pivots to raise significance stop improving held-out metrics",
        operator_auth_required=True,
        auto_downgrade_threshold="A->B after 2 failed novelty pivots OR best held-out win fails to beat baseline",
        source_anchor="PRL acceptance criteria; Nature Physics aims & scope",
    ),
    "B": TierSpec(
        tier="B", name="Solid PhD thesis / strong specialist journal (Communications Physics) — DEFAULT",
        claim_types=("bounded original contribution: a new mechanism/diagnostic/insight valid in a "
                     "limited model, parameter range, or regime (need not reshape the field)",),
        evidence_requirements=("a defensible, examinable new result with clear boundaries", "reproducible",
                               "a reproduced baseline for comparison", "honest scope"),
        numerical_requirements=("one model + a bounded parameter sweep", "convergence/robustness within the bound",
                                ">=1 held-out check"),
        reviewer_gate="'significant advance bringing new insight to a specialized area' / bounded PhD contribution",
        manuscript_gate="original-research article, bounded-scope framing, explicit limitations",
        downgrade_from_when="even a bounded positive original claim cannot be supported",
        stop_chasing_higher_when="the contribution is defensible and bounded — STOP; do not chase A/S",
        operator_auth_required=False,
        auto_downgrade_threshold="B->C after 1 failed cycle to establish a bounded positive claim",
        source_anchor="Communications Physics aims & scope; PhD 'original contribution' criteria",
    ),
    "C": TierSpec(
        tier="C", name="Ordinary PhD / general specialist journal (Scientific Reports / PRB-specialist)",
        claim_types=("technically sound systematic evaluation / benchmark", "failure-mechanism identification",
                     "a limited improvement", "a rigorous negative-leaning comparison"),
        evidence_requirements=("correct, reproducible methodology + analysis", "a reproduced baseline",
                               "clear boundaries", "honest reporting of what does and does not work"),
        numerical_requirements=("a systematic comparison/benchmark with controls + convergence checks", "provenance"),
        reviewer_gate="'scientifically valid and technically sound' ONLY — no importance/novelty bar at C",
        manuscript_gate="technically-sound-report package; honest, bounded, no overclaim",
        downgrade_from_when="no sound positive/benchmark result, but a systematic negative result can be produced",
        stop_chasing_higher_when="a sound benchmark / failure-mechanism study exists — STOP",
        operator_auth_required=False,
        auto_downgrade_threshold="C->D when all preregistered diagnostics are falsified but the falsification is systematic + reproducible",
        source_anchor="Scientific Reports guide to referees; PRB specialist scope",
    ),
    "D": TierSpec(
        tier="D", name="No-go / negative / null result (Registered-Report logic) — SUCCESS TERMINAL",
        claim_types=("bounded, honest negative result: 'under this model/regime, with these "
                     "preregistered diagnostics, correspondence is not restored / no method beats "
                     "baseline', with the failure characterized",),
        evidence_requirements=("sound question + sound method + honest reproducible negative evidence",
                               "falsified candidates recorded with numbers", "a reproduced baseline",
                               "held-out tests", "explicit no-overextrapolation"),
        numerical_requirements=("the falsifying comparisons with controls, scaling, and convergence — "
                                "enough to show the negative is real, not under-resourcing",),
        reviewer_gate="question-importance + method-soundness + honesty/boundedness (NOT 'did you find a positive effect')",
        manuscript_gate="bounded failure-regime / no-go manuscript; claims ledger of what was ruled out; strong limitations",
        downgrade_from_when="terminal — nothing below D",
        stop_chasing_higher_when="immediately — at D you STOP producing mechanisms and WRITE the paper",
        operator_auth_required=False,
        auto_downgrade_threshold="N/A (terminal)",
        source_anchor="COS Registered Reports; PLOS null/negative results; Scientific Reports (negatives welcome)",
    ),
}

DEFAULT_START_TIER = "B"


def _norm_tier(t: object) -> str:
    s = str(t or "").strip().upper()
    return s if s in TIERS else ""


def resolve_start_tier() -> str:
    """The tier the run starts at (env ``ARGUS_SKILL_PHYSICS_START_TIER``; default B)."""
    return _norm_tier(os.environ.get(ENV_START_TIER)) or DEFAULT_START_TIER


def nogo_terminal_enabled() -> bool:
    """Whether Tier-D no-go is an autonomous success terminal (default True)."""
    raw = (os.environ.get(ENV_NOGO_TERMINAL) or "").strip().lower()
    if raw in _FALSE:
        return False
    if raw in _TRUE:
        return True
    return True


def tier_spec(tier: object) -> TierSpec | None:
    return TIERS.get(_norm_tier(tier) or "", None)


def tier_index(tier: object) -> int:
    t = _norm_tier(tier)
    return TIER_ORDER.index(t) if t in TIER_ORDER else -1


def next_lower_tier(tier: object) -> str:
    """The next rung DOWN (S->A->B->C->D); D has no lower (returns '')."""
    i = tier_index(tier)
    if i < 0 or i >= len(TIER_ORDER) - 1:
        return ""
    return TIER_ORDER[i + 1]


def is_terminal_tier(tier: object) -> bool:
    return _norm_tier(tier) == "D"


def tier_rubric_banner(tier: object) -> str:
    """A prompt block stating the ACTIVE tier's acceptance bar, so downstream roles
    (esp. the reviewer) evaluate against THIS tier and not a higher one."""
    spec = tier_spec(tier)
    if spec is None:
        return ""
    return (
        f"## ACTIVE INNOVATION TIER — {spec.tier} ({spec.name})\n"
        f"- ACCEPTABLE CLAIM TYPES: {'; '.join(spec.claim_types)}.\n"
        f"- MIN EVIDENCE: {'; '.join(spec.evidence_requirements)}.\n"
        f"- NUMERICAL: {'; '.join(spec.numerical_requirements)}.\n"
        f"- REVIEWER GATE (evaluate against THIS tier only): {spec.reviewer_gate}.\n"
        f"- MANUSCRIPT GATE: {spec.manuscript_gate}.\n"
        f"- STOP CHASING HIGHER WHEN: {spec.stop_chasing_higher_when}.\n"
        f"- The reviewer MUST NOT apply a higher tier's standard to a Tier-{spec.tier} claim. "
        f"Downgrade is a change of CLAIM TYPE, never a reduction in rigor.\n"
    )


__all__ = [
    "ENV_START_TIER", "ENV_NOGO_TERMINAL", "TIER_ORDER", "TIERS", "TierSpec",
    "DEFAULT_START_TIER", "resolve_start_tier", "nogo_terminal_enabled", "tier_spec",
    "tier_index", "next_lower_tier", "is_terminal_tier", "tier_rubric_banner",
]
