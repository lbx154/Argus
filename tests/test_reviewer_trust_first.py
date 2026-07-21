"""Reviewer trust-first verification stance (operator directive 2026-06-26).

Root cause fixed: the reviewer prompt unconditionally told the reviewer to
re-run the engineer's commands itself and use *its own* output as ground truth.
On a trusted-scorer task that meant re-running the official scorer EVERY round
to re-confirm a number the engineer already obtained from that same frozen
scorer — burning the round for zero value and treating a no-reward engineer as
a suspect.

New stance (global): TRUST an honest, internally-consistent self-report; verify
only when evidence is MISSING or self-contradictory (the cheap anti-fabrication
floor that still stops a faked number); reinvest the round in judging the idea's
novelty + giving high-altitude direction. In MEASURED-BENCHMARK mode this is
sharpened and explicitly overrides the generic demand-evidence rules.
"""
from __future__ import annotations

from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._core import _verification_directive


def _prompt(*, measured: bool, monkeypatch) -> str:
    if measured:
        monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
    else:
        monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize cand_ms on the kernel",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="HANDOFF: tried X. RESULT correct=true cand_ms=0.5",
        main_error=None,
        prior_checkpoint={},
    )


def test_directive_trusts_and_drops_reflexive_rerun():
    d = _verification_directive()
    assert "Trust consistent shown results" in d
    assert "missing" in d
    assert "contradictory" in d
    assert "next step" in d
    assert len(d) < 220
    # the OLD reflexive "use your own output as ground truth" framing is gone
    assert "use *your own* output as ground truth" not in d


def test_build_prompt_uses_trust_first_not_old_rerun(monkeypatch):
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Trust consistent shown results" in p
    assert "use *your own* output as ground truth" not in p
    assert "## Evidence policy" not in p


def test_measured_mode_trusts_scorer_and_refocuses(monkeypatch):
    p = _prompt(measured=True, monkeypatch=monkeypatch)
    assert "TRUST the scorer, judge the IDEA" in p
    assert "Do NOT re-run the scorer yourself" in p
    assert "self-supervises correctness" in p
    # refocus on novelty judgement + high-level direction
    assert "genuinely novel" in p
    # explicit override of the generic demand-evidence rules
    assert "OVERRIDES the generic" in p


def test_non_measured_keeps_anti_fabrication_floor(monkeypatch):
    # Trust-first must NOT remove the floor: the reviewer still defaults to
    # `continue` (not `done`) when a claim is NOT backed by shown evidence.
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Default to `continue` whenever the agent's claims are not backed" in p


def test_reviewer_reasons_in_prose_structured_only_at_handoff(monkeypatch):
    # The reviewer must talk in natural language during its turn and emit the
    # structured JSON ONLY as the final handoff — not format every message as
    # JSON. codex --output-schema constrains only the FINAL response, so this is
    # a prompt-framing change with no robustness loss.
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "talk normally" in p.lower()
    assert "ONLY your FINAL message is the structured handoff" in p
    assert "intermediate messages" in p
    assert "FINAL handoff JSON object" in p
    # the old "every message is JSON" framing is gone
    assert "Return valid JSON matching the provided schema" not in p
