"""Stage-aware SETUP action control in the engineer prompt builder.

At the `setup` stage (pre-optimize) the engineer prompt must inject a hard
override that suppresses the optimize framing's pull and forbids scoring-to-
tune / recipe edits — its only deliverable is profiling + the ground-truth
gate. The rule is GENERAL (keyed on the stage name, not on any task).
"""
from __future__ import annotations

from argus_skill import loop as loop_mod
from argus_skill.loop import SkillLoop


def _prompt() -> str:
    return SkillLoop._build_engineer_prompt(
        task="Make the artifact better under a fixed budget.",
        skill_text="",
        next_action=None,
        extra_guidance=None,
        paper_mission=False,
    )


def test_setup_stage_injects_action_control(monkeypatch) -> None:
    # Force the active stage to `setup` regardless of the live project state.
    monkeypatch.setattr(
        "argus_skill.skills.stage_checklists.current_stage",
        lambda *_a, **_k: "setup",
    )
    out = _prompt()
    assert "## SETUP STAGE — action control (HARD OVERRIDE)" in out
    low = out.lower()
    # Forbids scoring-to-tune and recipe edits to chase the score.
    assert "eval_solution.sh" in low
    assert "forbidden" in low
    # Only deliverables: profiling + the ground-truth gate.
    assert "profile" in low
    assert "GROUND_TRUTH.md" in out
    # General: keyed on the stage, no task-specific literals leak in.
    for lit in ("nanochat", "nanogpt", "mfu", "bpb", "a100"):
        assert lit not in low


def test_non_setup_stage_has_no_setup_action_control(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.skills.stage_checklists.current_stage",
        lambda *_a, **_k: "optimize",
    )
    out = _prompt()
    assert "## SETUP STAGE — action control (HARD OVERRIDE)" not in out


def test_setup_control_survives_stage_read_failure(monkeypatch) -> None:
    # A broken stage read must not raise and must not inject the setup block.
    def _boom(*_a, **_k):
        raise RuntimeError("state unreadable")

    monkeypatch.setattr(
        "argus_skill.skills.stage_checklists.current_stage", _boom
    )
    out = _prompt()
    assert isinstance(out, str) and out
    assert "## SETUP STAGE — action control (HARD OVERRIDE)" not in out
