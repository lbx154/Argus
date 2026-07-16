"""Integration coverage for the direct-edit CHECKPOINT.md baton."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

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


def _review(status: str) -> str:
    return json.dumps({
        "status": status,
        "reason": "reviewed",
        "next_action": "continue" if status == "continue" else "—",
        "round_summary_markdown": "# review\n",
        "completion_summary_markdown": "done" if status == "done" else "",
    })


def test_engineer_and_reviewer_edit_one_shared_checkpoint_in_sequence(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))

    def engineer_one(_prompt, _options) -> str:
        assert checkpoint.exists()
        assert "# Goal" in checkpoint.read_text(encoding="utf-8")
        checkpoint.write_text("# Current State\n\nEngineer round 1\n", encoding="utf-8")
        return "round 1 work"

    def reviewer_one(_prompt, _options) -> str:
        assert "Engineer round 1" in checkpoint.read_text(encoding="utf-8")
        checkpoint.write_text(
            "# Current State\n\nReviewer accepted round 1\n\n"
            "# Next Action\n\nContinue from reviewed state\n",
            encoding="utf-8",
        )
        return _review("continue")

    def engineer_two(_prompt, _options) -> str:
        text = checkpoint.read_text(encoding="utf-8")
        assert "Reviewer accepted round 1" in text
        checkpoint.write_text(
            text.replace("Continue from reviewed state", "Engineer finished work"),
            encoding="utf-8",
        )
        return "round 2 work"

    def reviewer_two(_prompt, _options) -> str:
        assert "Engineer finished work" in checkpoint.read_text(encoding="utf-8")
        checkpoint.write_text(
            "# Current State\n\nReviewer certified completion\n",
            encoding="utf-8",
        )
        return _review("done")

    backend.queue("engineer-r1", CannedResponse(message_factory=engineer_one))
    backend.queue("reviewer", CannedResponse(message_factory=reviewer_one))
    backend.queue("engineer-r2", CannedResponse(message_factory=engineer_two))
    backend.queue("reviewer", CannedResponse(message_factory=reviewer_two))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=3, checkpoint_path=checkpoint),
    )
    outcome = loop.run("demo task", workdir=tmp_path)

    assert outcome.successful
    assert checkpoint.read_text(encoding="utf-8").endswith(
        "Reviewer certified completion\n"
    )
    role_resumes = [
        (label, tid)
        for label, tid in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ]
    assert role_resumes == [
        ("engineer-r1", None),
        ("reviewer", None),
        ("engineer-r2", None),
        ("reviewer", None),
    ]

    prompts = {label: prompt for label, prompt, _ in backend.history}
    assert str(checkpoint.resolve()) in prompts["engineer-r1"]
    reviewer_prompts = [p for label, p, _ in backend.history if label == "reviewer"]
    assert all(str(checkpoint.resolve()) in prompt for prompt in reviewer_prompts)
    assert all("do not emit checkpoint JSON" in prompt for prompt in reviewer_prompts)


def test_reviewer_output_does_not_need_checkpoint_json(tmp_path: Path) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="work"))
    backend.queue("reviewer", CannedResponse(message=_review("done")))

    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, checkpoint_path=checkpoint),
    ).run("demo task", workdir=tmp_path)

    assert outcome.successful
    assert checkpoint.exists()
