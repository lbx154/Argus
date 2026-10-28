"""The Reviewer judges every Engineer turn since its last review, not only the last."""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _reviewer_prompts(backend: MemoryBackend) -> list[str]:
    return [prompt for label, prompt, _options in backend.history if label == "reviewer"]


def _changed_files(prompt: str) -> str:
    return prompt.split("## Workspace changes under review", 1)[1].split("Diff excerpt:", 1)[0]


def _config(tmp_path: Path) -> SupervisedConfig:
    return SupervisedConfig(
        max_rounds=4, require_independent_review=True, session_id="m1",
        state_root=tmp_path / "state",
    )


def test_a_later_review_still_sees_the_whole_task(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    def round_one(_prompt, _options) -> str:
        (work / "evaluate.py").write_text("SPLIT = 'test'\n")
        return "Fixed the split (MARKER_R1)."

    def round_two(_prompt, _options) -> str:
        (work / "report.md").write_text("accuracy 0.81\n")
        return "Wrote report.md."

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(action=hand_off("reviewer"), message_factory=round_one, thread_id="t1"))
    backend.queue("engineer-r2", CannedResponse(action=hand_off("reviewer"), message_factory=round_two, thread_id="t2"))
    backend.queue(
        "reviewer",
        CannedResponse(action=hand_off("engineer", "Now write the report.")),
    )
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "ok")))

    status, *_ = _engineer(backend).run(
        objective="fix the split and report accuracy",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=_config(tmp_path),
        workdir=work,
    )

    assert status == "done"
    second = _reviewer_prompts(backend)[1]
    # The accounts a review has read are not repeated, but the diff still runs
    # from the task's start: only a pass may move the baseline (HO-7c).
    assert "Earlier Engineer turns since the last review" not in second
    changed = _changed_files(second)
    assert "- evaluate.py: +1 -0" in changed
    assert "- report.md: +1 -0" in changed
