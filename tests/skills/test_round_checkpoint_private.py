from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from argus_skill.core.models import ReviewDecision, RoundRecord
from argus_skill.loop import SkillLoopConfig
from argus_skill.skills.loop_review_hooks import ReviewedRoundHooksMixin
from argus_skill.skills.loop_state import MissionContext
from argus_skill.skills.round_checkpoint import checkpoint_round


def git(root: Path, *args: str, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    assert git(root, "init", "-q").returncode == 0
    assert git(root, "checkout", "-q", "-b", "feature").returncode == 0
    git(root, "config", "user.name", "User")
    git(root, "config", "user.email", "user@example.com")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(root, "add", "seed.txt")
    git(root, "commit", "-q", "-m", "seed")
    return root


def test_checkpoint_writes_private_ref_without_touching_head_or_index(tmp_path) -> None:
    root = repo(tmp_path)
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(root, "add", "staged.txt")
    staged_before = git(root, "diff", "--cached", "--name-only").stdout
    head_before = git(root, "rev-parse", "HEAD").stdout
    (root / "round.txt").write_text("round\n", encoding="utf-8")

    result = checkpoint_round(
        root,
        mission_id="mission-1",
        round_index=2,
        message="Reviewer recommends a checkpoint",
    )

    assert result.recorded is True
    assert result.ref == "refs/argus/checkpoints/mission-1/round-0002"
    assert git(root, "rev-parse", "HEAD").stdout == head_before
    assert git(root, "diff", "--cached", "--name-only").stdout == staged_before
    assert git(root, "show", f"{result.ref}:round.txt").stdout == "round\n"


def test_checkpoint_is_a_noop_without_worktree_changes(tmp_path) -> None:
    root = repo(tmp_path)
    result = checkpoint_round(root, mission_id="m", round_index=1, message="nothing")
    assert result.recorded is False
    assert result.error == ""


class Loop(ReviewedRoundHooksMixin):
    def __init__(self, enabled: bool) -> None:
        self.config = SkillLoopConfig(round_checkpoint_enabled=enabled)
        self.events: list[dict] = []

    def _emit(self, event: dict) -> None:
        self.events.append(event)


def mission(root: Path) -> MissionContext:
    values = {
        "workdir": root,
        "run_id": "run-1",
        "task": "task",
        "skill_task": "task",
        "active_vertical": "software",
        "seed_thread_id": None,
    }
    kwargs = {}
    for field in dataclasses.fields(MissionContext):
        if field.name in values:
            kwargs[field.name] = values[field.name]
        elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            kwargs[field.name] = ""
    return MissionContext(**kwargs)


def record(recommended: bool) -> RoundRecord:
    return RoundRecord(
        round_index=1,
        engineer_message="work",
        engineer_exit_code=0,
        review=ReviewDecision(
            status="continue",
            reason="Progress is worth preserving",
            next_action="continue",
            checkpoint_recommended=recommended,
        ),
    )


def test_only_explicit_reviewer_recommendation_records_checkpoint(tmp_path) -> None:
    root = repo(tmp_path)
    (root / "work.txt").write_text("work\n", encoding="utf-8")
    loop = Loop(enabled=True)

    loop._capture_reviewed_round(mission(root), record(False))
    assert loop.events == []
    loop._capture_reviewed_round(mission(root), record(True))

    assert [event["type"] for event in loop.events] == ["round.checkpoint.recorded"]
