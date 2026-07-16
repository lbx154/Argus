"""Tests for the ordinary shared CHECKPOINT.md helper."""

from pathlib import Path

from argus_skill.engineer.checkpoint import (
    SHARED_CHECKPOINT_TEMPLATE,
    ensure_shared_checkpoint,
    shared_checkpoint_instructions,
)


def test_ensure_shared_checkpoint_creates_plain_markdown(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    assert ensure_shared_checkpoint(path) == path.resolve()
    assert path.read_text(encoding="utf-8") == SHARED_CHECKPOINT_TEMPLATE


def test_ensure_shared_checkpoint_never_rewrites_agent_edits(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    path.write_text("reviewer state\n", encoding="utf-8")
    ensure_shared_checkpoint(path)
    assert path.read_text(encoding="utf-8") == "reviewer state\n"


def test_none_disables_shared_checkpoint() -> None:
    assert ensure_shared_checkpoint(None) is None
    assert shared_checkpoint_instructions(None, role="engineer") == ""


def test_engineer_and_reviewer_receive_direct_edit_instructions(tmp_path: Path) -> None:
    path = tmp_path / "CHECKPOINT.md"
    engineer = shared_checkpoint_instructions(path, role="engineer")
    reviewer = shared_checkpoint_instructions(path, role="reviewer")

    assert str(path.resolve()) in engineer
    assert "previous Reviewer edited it last" in engineer
    assert "Engineer already edited it this round" in reviewer
    assert "the final editor" in reviewer
    assert "do not emit checkpoint JSON" in reviewer
