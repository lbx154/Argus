"""The Reviewer judges every Engineer turn since its last review, not only the last."""
from __future__ import annotations

import time
from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.round_evidence import RoundEvidence
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _reviewer_prompts(backend: MemoryBackend) -> list[str]:
    return [prompt for label, prompt, _options in backend.history if label == "reviewer"]


def _modified_files_line(prompt: str) -> str:
    return next(line for line in prompt.splitlines() if "Workspace files modified" in line)


def test_reviewer_sees_the_self_handoff_turn_before_the_final_one(tmp_path: Path) -> None:
    def round_one(_prompt, _options) -> str:
        # File timestamps come from a coarse kernel clock; a write in the same
        # tick the window opened can read as older than it.
        time.sleep(0.05)
        (tmp_path / "evaluate.py").write_text("SPLIT = 'train'\n")
        return (
            "Switched evaluate.py to score on the train split (MARKER_R1).\n"
            "MILESTONE_STATUS=continue\nNEXT_OWNER=engineer"
        )

    def round_two(_prompt, _options) -> str:
        time.sleep(0.05)
        (tmp_path / "report.md").write_text("accuracy 0.99\n")
        return "Wrote report.md with accuracy 0.99.\nMILESTONE_STATUS=done\nNEXT_OWNER=reviewer"

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message_factory=round_one, thread_id="t1"))
    backend.queue("engineer-r2", CannedResponse(message_factory=round_two, thread_id="t2"))
    backend.queue(
        "reviewer",
        CannedResponse(review_action=("approve_review", {"review": "ok"}), thread_id="v1"),
    )

    status, _rounds, *_ = _engineer(backend).run(
        objective="train and report accuracy",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=True),
        workdir=tmp_path,
    )

    assert status == "done"
    [prompt] = _reviewer_prompts(backend)
    assert "Earlier Engineer turns since the last review" in prompt
    assert "MARKER_R1" in prompt
    modified = _modified_files_line(prompt)
    assert "evaluate.py" in modified
    assert "report.md" in modified


def test_a_completed_review_closes_the_window(tmp_path: Path) -> None:
    def round_one(_prompt, _options) -> str:
        (tmp_path / "evaluate.py").write_text("SPLIT = 'test'\n")
        return "Fixed the split (MARKER_R1).\nMILESTONE_STATUS=continue\nNEXT_OWNER=reviewer"

    def round_two(_prompt, _options) -> str:
        time.sleep(0.05)
        (tmp_path / "report.md").write_text("accuracy 0.81\n")
        return "Wrote report.md.\nMILESTONE_STATUS=done\nNEXT_OWNER=reviewer"

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message_factory=round_one, thread_id="t1"))
    backend.queue("engineer-r2", CannedResponse(message_factory=round_two, thread_id="t2"))
    backend.queue(
        "reviewer",
        CannedResponse(review_action=("revise_review", {"review": "Now write the report."})),
    )
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "ok"})))

    status, *_ = _engineer(backend).run(
        objective="fix the split and report accuracy",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=True),
        workdir=tmp_path,
    )

    assert status == "done"
    second = _reviewer_prompts(backend)[1]
    assert "Earlier Engineer turns since the last review" not in second
    modified = _modified_files_line(second)
    assert "report.md" in modified
    assert "evaluate.py" not in modified


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
        CannedResponse(message="Step one.\nMILESTONE_STATUS=continue\nNEXT_OWNER=engineer"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message="Step two.\nMILESTONE_STATUS=done\nNEXT_OWNER=reviewer"),
    )
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "ok"})))

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


def test_engineer_cannot_hand_itself_the_work_indefinitely(tmp_path: Path) -> None:
    backend = MemoryBackend(
        default=CannedResponse(message="still working\nMILESTONE_STATUS=continue\nNEXT_OWNER=engineer")
    )
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "ok"})))

    status, rounds, *_ = _engineer(backend).run(
        objective="keep going",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=10, require_independent_review=True),
        workdir=tmp_path,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:4] == ["engineer-r1", "engineer-r2", "engineer-r3", "reviewer"]
    assert status == "done"
    assert len(rounds) == 1
    [prompt] = _reviewer_prompts(backend)
    assert "### Engineer round 1" in prompt
    assert "### Engineer round 2" in prompt
