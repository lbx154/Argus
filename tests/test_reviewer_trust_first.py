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

import json

from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._core import _verification_directive
from argus_skill.verticals.research.prompt_policy import academic_paper_review_block


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
    assert "Trust clear, consistent evidence" in d
    assert "missing" in d
    assert "contradictory" in d
    assert "identity drift" in d.lower()
    assert "git diff" in d.lower()
    assert "hashes" not in d.lower()
    assert len(d) < 420
    # the OLD reflexive "use your own output as ground truth" framing is gone
    assert "use *your own* output as ground truth" not in d


def test_paper_review_requires_idea_and_built_artifact_quality():
    block = academic_paper_review_block()

    # Truth is necessary and not sufficient: the PC reviewer owns the separate
    # judgement of whether the idea is worth the venue, and can reopen a sound
    # local plan through the existing Manager-adjudicated channel.
    assert "central research idea is novel" in block
    assert "small scoreboard gain is not paper-level success" in block
    assert "`plan_signal` to `reconsider`" in block
    assert "`plan_alternative`" in block
    assert "same selector is not a bolder plan" in block
    assert "cross-model, cross-benchmark" in block

    assert "undefined citations" in block
    assert "bibliography warnings" in block
    assert "overfull boxes" in block
    assert "PDF title/author metadata" in block
    assert "Render the relevant pages" in block


def _persist_review_stage(tmp_path, vertical: str) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, vertical)
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "review"
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _project_reviewer_prompt(tmp_path, *, scope: str = "") -> tuple[str, Reviewer]:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="review the current result",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="RESULT: checked evidence",
        main_error=None,
        prior_checkpoint={},
        working_dir=str(tmp_path),
        scope=scope,
    )
    return prompt, reviewer


def test_math_review_omits_paper_review_rubric(tmp_path) -> None:
    _persist_review_stage(tmp_path, "math")

    prompt, reviewer = _project_reviewer_prompt(tmp_path)

    assert "## Near-complete paper review" not in prompt
    assert reviewer.last_prompt_block_stats["static_total"]["chars"] > 0


def test_final_certification_review_keeps_paper_review_rubric(tmp_path) -> None:
    _persist_review_stage(tmp_path, "research")

    prompt, reviewer = _project_reviewer_prompt(tmp_path)

    assert "## Near-complete paper review" in prompt
    assert reviewer.last_prompt_block_stats["static_total"]["chars"] > 0


def test_final_submission_does_not_make_math_a_paper_vertical(tmp_path) -> None:
    _persist_review_stage(tmp_path, "math")

    prompt, _reviewer = _project_reviewer_prompt(
        tmp_path,
        scope="final_submission",
    )

    assert "## Near-complete paper review" not in prompt
    assert "## Final paper review" not in prompt


def test_certified_medical_review_does_not_inherit_paper_policy(tmp_path) -> None:
    _persist_review_stage(tmp_path, "medical")

    prompt, _reviewer = _project_reviewer_prompt(
        tmp_path,
        scope="final_submission",
    )

    assert "## Near-complete paper review" not in prompt
    assert "## Final paper review" not in prompt


def test_build_prompt_uses_trust_first_not_old_rerun(monkeypatch):
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Trust clear, consistent evidence" in p
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


def test_non_measured_blocks_only_on_claim_critical_evidence(monkeypatch):
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Only missing claim-critical evidence means `continue`" in p
    assert "optional evidence and minor weaknesses stay advisory" in p


def test_done_is_default_for_materially_complete_outcome(monkeypatch):
    p = _prompt(measured=False, monkeypatch=monkeypatch)

    assert "Default to `done`" in p
    assert "not exhaustive proof or artifact completeness" in p
    assert "Current operator > objective > mission" in p
    assert "One timeout, failed attempt" in p


def test_reviewer_separates_integrity_from_scientific_value(monkeypatch):
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Integrity is mandatory" in p
    assert "not scientific value by itself" in p
    assert "`replan_requested` rarely" in p


def test_reviewer_reasons_in_prose_structured_only_at_handoff(monkeypatch):
    # The reviewer must talk in natural language during its turn and carry
    # structure ONLY at the final handoff. Since 2026-07-26 that handoff is a
    # few named lines rather than a schema-constrained JSON object, which makes
    # the property stronger, not weaker: the prose and the verdict now live in
    # the same message instead of the verdict replacing it.
    p = _prompt(measured=False, monkeypatch=monkeypatch)
    assert "Any later response is plain language" in p
    assert "ARGUS_ROLE_DECISION=" in p
    assert '"status":"done"' in p
