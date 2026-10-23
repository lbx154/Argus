"""The Reviewer judges every Engineer turn since its last review, not only the last."""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.round_evidence import RoundEvidence
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


def test_reviewer_sees_the_self_handoff_turn_before_the_final_one(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    def round_one(_prompt, _options) -> str:
        (work / "evaluate.py").write_text("SPLIT = 'train'\n")
        return ""

    def round_two(_prompt, _options) -> str:
        (work / "report.md").write_text("accuracy 0.99\n")
        return ""

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message_factory=round_one, thread_id="t1", action=hand_off(
        "engineer", "Switched the split.", "Switched evaluate.py to score on the train split (MARKER_R1).",
    )))
    backend.queue("engineer-r2", CannedResponse(message_factory=round_two, thread_id="t2", action=hand_off(
        "reviewer", "Wrote report.md with accuracy 0.99.",
    )))
    backend.queue(
        "reviewer",
        CannedResponse(action=hand_off("done", "ok"), thread_id="v1"),
    )

    status, _rounds, *_ = _engineer(backend).run(
        objective="train and report accuracy",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=_config(tmp_path),
        workdir=work,
    )

    assert status == "done"
    [prompt] = _reviewer_prompts(backend)
    assert "Earlier Engineer turns since the last review" in prompt
    assert "MARKER_R1" in prompt
    changed = _changed_files(prompt)
    assert "- evaluate.py: +1 -0" in changed
    assert "- report.md: +1 -0" in changed


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
    backend.queue("engineer-r1", CannedResponse(message_factory=round_one, thread_id="t1"))
    backend.queue("engineer-r2", CannedResponse(message_factory=round_two, thread_id="t2"))
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


def test_unreviewed_turn_evidence_waits_for_the_reviewer(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "argus.engineer.round_evidence.collect_round_evidence",
        lambda request: [
            RoundEvidence(reviewer_text=f"probe-{request.round_index} fired", provider="p")
        ],
    )
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(action=hand_off("engineer", "Step one.")),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(action=hand_off("reviewer", "Step two.")),
    )
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "ok")))

    status, *_ = _engineer(backend).run(
        objective="two-step task",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=True),
        workdir=tmp_path,
    )

    assert status == "done"
    [prompt] = _reviewer_prompts(backend)
    assert "probe-1 fired" in prompt
    assert "probe-2 fired" in prompt
    assert prompt.index("probe-1 fired") < prompt.index("probe-2 fired")


def test_the_reviewer_judges_every_turn_the_engineer_kept_for_itself(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for index in (1, 2):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(action=hand_off("engineer", f"step {index} written")),
        )
    backend.queue("engineer-r3", CannedResponse(message="all steps written and checked"))
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "ok")))

    status, rounds, *_ = _engineer(backend).run(
        objective="keep going",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=10, require_independent_review=True),
        workdir=tmp_path,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels == ["engineer-r1", "engineer-r2", "engineer-r3", "reviewer"]
    assert status == "done"
    assert len(rounds) == 1
    [prompt] = _reviewer_prompts(backend)
    assert "### Engineer round 1" in prompt
    assert "### Engineer round 2" in prompt
