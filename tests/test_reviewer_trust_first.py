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
novelty + giving high-altitude direction.
"""

from __future__ import annotations

import json

from argus.reviewer import Reviewer
from argus.roles.prompts.reviewer import _verification_directive


def test_directive_trusts_and_drops_reflexive_rerun():
    d = _verification_directive()
    assert 'Trust consistent evidence' in d
    assert 'gaps' in d
    assert 'contradictions' in d
    assert "identity drift" in d.lower()
    assert "git diff" in d.lower()
    assert "hashes" not in d.lower()
    assert len(d) < 420
    # the OLD reflexive "use your own output as ground truth" framing is gone
    assert "use *your own* output as ground truth" not in d


def _persist_review_stage(tmp_path, vertical: str) -> None:
    from argus.skills.vertical_select import persist_vertical

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


def test_non_paper_review_omits_paper_review_rubric(tmp_path) -> None:
    _persist_review_stage(tmp_path, "learning")

    prompt, reviewer = _project_reviewer_prompt(tmp_path)

    assert "## Near-complete paper review" not in prompt
    assert reviewer.last_prompt_block_stats["static_total"]["chars"] > 0


def test_final_submission_does_not_make_a_non_paper_vertical_a_paper_one(tmp_path) -> None:
    _persist_review_stage(tmp_path, "learning")

    prompt, _reviewer = _project_reviewer_prompt(
        tmp_path,
        scope="final_submission",
    )

    assert "## Near-complete paper review" not in prompt
    assert "## Final paper review" not in prompt
