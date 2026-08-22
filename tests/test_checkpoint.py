"""Tests for the ordinary shared CHECKPOINT.md helper."""

from pathlib import Path

from argus_skill.engineer.checkpoint import (
    resolve_shared_checkpoint,
    shared_checkpoint_instructions,
)


def test_resolve_shared_checkpoint_does_not_create_file(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    assert resolve_shared_checkpoint(path) == path.resolve()
    assert not path.exists()


def test_resolve_shared_checkpoint_never_rewrites_agent_edits(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    path.write_text("reviewer state\n", encoding="utf-8")
    resolve_shared_checkpoint(path)
    assert path.read_text(encoding="utf-8") == "reviewer state\n"


def test_none_disables_shared_checkpoint() -> None:
    assert resolve_shared_checkpoint(None) is None
    assert shared_checkpoint_instructions(None, role="engineer") == ""


def test_engineer_and_reviewer_receive_direct_edit_instructions(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    engineer = shared_checkpoint_instructions(path, role="engineer")
    reviewer = shared_checkpoint_instructions(path, role="reviewer")

    assert str(path.resolve()) in engineer
    assert "Role-state index" in engineer
    assert "current prompt already contains the mission contract" in engineer
    assert "another round needs" in engineer
    assert "Reviewer is the final editor" in reviewer
    assert "not a log or JSON verdict" in reviewer


def _assemble(round_index: int, checkpoint: Path | None, workdir: Path) -> str:
    """Drive the real prompt assembly for one round with everything else inert."""
    from types import SimpleNamespace

    from argus_skill.engineer.round_prompt import RoundPromptMixin

    role_session = SimpleNamespace(
        policy="fresh", action="fresh", prompt_block=lambda: ""
    )
    config = SimpleNamespace(
        compact_continuation_prompts=True,
        background_subagent_advisory=False,
        max_rounds=8,
    )
    return RoundPromptMixin()._assemble_round_prompt(
        round_index=round_index,
        supervised_config=config,
        engineer_prompt_builder=lambda _delta, _static: "TASK",
        reviewer_next_action=None,
        checkpoint_path=checkpoint,
        workdir=workdir,
        role_session=role_session,
        on_event=None,
    )


def test_round_one_is_told_the_shared_checkpoint_exists(tmp_path: Path) -> None:
    """The baton must be written by the round BEFORE the one that reads it.

    Gating this block on ``round_index > 1`` meant round 1 never heard of the
    file, yet its sealed handoff record advertised ``checkpoint.path`` and
    round 2 was told to read it. Round 2 opened a missing file and restarted
    the mission from nothing; round 1's findings only ever existed in a
    provider context that had since been rotated away.
    """
    checkpoint = tmp_path / "CHECKPOINT.md"

    prompt = _assemble(1, checkpoint, tmp_path)

    assert "## Shared checkpoint" in prompt
    assert str(checkpoint.resolve()) in prompt


def test_round_one_checkpoint_guidance_stays_conditional(tmp_path: Path) -> None:
    """Telling round 1 about the file must not conscript a one-round mission
    into writing one — the instruction carries its own trigger."""
    prompt = _assemble(1, tmp_path / "CHECKPOINT.md", tmp_path)

    assert "only when another round needs" in prompt


def test_a_later_round_still_gets_the_checkpoint(tmp_path: Path) -> None:
    prompt = _assemble(3, tmp_path / "CHECKPOINT.md", tmp_path)

    assert "## Shared checkpoint" in prompt


def test_disabled_checkpoint_adds_no_block_on_round_one(tmp_path: Path) -> None:
    """``ARGUS_SKILL_CHECKPOINT_PERSIST=0`` resolves the path to ``None``; the
    unconditional block must stay silent rather than cite a path that is off."""
    prompt = _assemble(1, None, tmp_path)

    assert "## Shared checkpoint" not in prompt
