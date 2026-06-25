"""The reviewer must author + carry forward the checkpoint `active_line`, and
its next_step contract must default to CONTINUING an active line rather than
restoring the floor — the structural fix for the greedy restore-the-floor rut.
"""
from __future__ import annotations

from argus_skill.engineer.checkpoint import CheckpointState
from argus_skill.reviewer import Reviewer, _parse_checkpoint


def _prompt(prior_checkpoint=None) -> str:
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize val_bpb",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="(handoff)",
        main_error=None,
        checks=[],
        prior_checkpoint=prior_checkpoint or {},
    )


def test_output_contract_lists_active_line():
    # The reviewer was previously never told active_line exists, so it could
    # never author it (it stayed permanently empty). It must be in the schema.
    p = _prompt()
    assert "active_line{desc" in p


def test_authoring_inverts_the_default_away_from_restore():
    p = _prompt()
    assert "THE DEFAULT, INVERTED" in p
    # next_step must be tied to continuing the active line, not restoring floor
    assert "MUST be to CONTINUE developing" in p
    assert "restore the\n" in p or "restore the global-best floor" in p


def test_prior_active_line_is_shown_to_reviewer():
    p = _prompt({"active_line": {"desc": "split-head raw-V",
                                 "branch_or_path": "active/a285",
                                 "rounds_active": 2, "note": "widen head"}})
    assert "active/a285" in p
    assert "split-head raw-V" in p
    assert "do NOT restore the floor while it is alive" in p


def test_reviewer_emitted_active_line_survives_parse_roundtrip():
    # reviewer JSON -> _parse_checkpoint -> CheckpointState.from_dict must keep it
    parsed = {"checkpoint": {
        "goal": "g",
        "active_line": {"desc": "co-tuned residual reshape",
                        "branch_or_path": "active/a286",
                        "rounds_active": 3, "note": "next: scale gate"},
        "next_step": "continue active line from active/a286",
    }}
    raw = _parse_checkpoint(parsed)
    cp = CheckpointState.from_dict(raw)
    assert cp.active_line["desc"] == "co-tuned residual reshape"
    assert cp.active_line["branch_or_path"] == "active/a286"
    assert cp.active_line["rounds_active"] == 3
    # and it renders for the next engineer with the build-on-it framing
    rendered = cp.render_for_engineer()
    assert "ACTIVE LINE" in rendered
    assert "do NOT restart" in rendered
