"""Tests for the ordinary shared CHECKPOINT.md helper."""

from pathlib import Path

from argus.engineer.checkpoint import (
    shared_checkpoint_instructions,
)


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
