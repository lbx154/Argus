"""Integration coverage for the direct-edit CHECKPOINT.md baton."""
from __future__ import annotations

from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from tests.hand_off import hand_off

SKILL_MD = (
    "## Title\nDemo skill\n\n"
    "## Description\nCheckpoint test.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- demo\n\n"
    "## When NOT to use\n- never\n\n"
    "## How to solve\n- work\n\n"
    "## Examples\n- demo\n\n"
    "## Response shape\n- concise\n"
)


def _review(status: str) -> tuple[str, dict]:
    return hand_off("engineer" if status == "continue" else "done", "reviewed", "continue")


def test_reviewer_output_does_not_need_checkpoint_json(tmp_path: Path) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="work"))
    backend.queue("reviewer", CannedResponse(action=_review("done")))

    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, checkpoint_path=checkpoint),
    ).run("demo task", workdir=tmp_path)

    assert outcome.successful
    assert not checkpoint.exists()
