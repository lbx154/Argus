"""Direct checkpoint editing plus legacy checkpoint parsing compatibility."""
from __future__ import annotations

from argus_skill.reviewer import Reviewer, _parse_checkpoint, parse_decision_text


def _prompt(checkpoint_path: str = "/tmp/project/CHECKPOINT.md") -> str:
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize val_bpb",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="(handoff)",
        main_error=None,
        checkpoint_path=checkpoint_path,
    )


def test_reviewer_is_told_to_edit_shared_checkpoint_directly():
    p = _prompt()
    assert "/tmp/project/CHECKPOINT.md" in p
    assert "edit the file directly" in p
    assert "do not emit checkpoint JSON" in p


def test_reviewer_is_the_final_checkpoint_editor_for_the_round():
    p = _prompt()
    assert "Engineer already edited it this round" in p
    assert "the final editor" in p


def test_checkpoint_state_is_not_copied_into_the_prompt():
    p = _prompt()
    assert "CURATED WORKING MEMORY" not in p
    assert "tried_and_failed" not in p


def test_legacy_checkpoint_parser_remains_backward_compatible():
    parsed = {"checkpoint": {
        "goal": "g",
        "active_line": {"desc": "co-tuned residual reshape",
                        "branch_or_path": "active/a286",
                        "rounds_active": 3, "note": "next: scale gate"},
        "next_step": "continue active line from active/a286",
    }}
    raw = _parse_checkpoint(parsed)
    assert raw["active_line"]["desc"] == "co-tuned residual reshape"
    assert raw["active_line"]["branch_or_path"] == "active/a286"
    assert raw["active_line"]["rounds_active"] == 3


def test_reviewer_output_without_confidence_parses_into_verdict():
    # The reviewer no longer self-reports a confidence. A structured output that
    # omits ``confidence`` entirely must still parse into a full verdict — the
    # parser must not depend on a confidence field to render a decision.
    raw = (
        '{"status": "done", "reason": "objective met with verified evidence", '
        '"next_action": "No further action needed.", '
        '"round_summary_markdown": "# Review\\n\\n- all checks passed\\n", '
        '"completion_summary_markdown": "Mission complete."}'
    )
    decision = parse_decision_text(raw)
    assert decision is not None
    assert decision.status == "done"
    assert decision.reason == "objective met with verified evidence"
    # The parsed verdict carries no confidence attribute at all.
    assert not hasattr(decision, "confidence")
