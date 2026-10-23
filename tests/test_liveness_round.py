"""Round-loop liveness: the baton goes where the roles send it.

Each test pins one confirmed defect where a role's own word was misread, or
where the text a role reads disagreed with the decision the host took.
"""
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


def _labels(backend: MemoryBackend) -> list[str]:
    return [label for label, _prompt, _options in backend.history]


# --- 1. Self-review mode: the Engineer finishes on its own line ---------


def test_unfinished_self_reviewed_turns_go_on_until_the_engineer_says_done(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for index in (1, 2, 3):
        backend.queue(f"engineer-r{index}", CannedResponse(action=hand_off("engineer", f"Step {index} written.")))
    backend.queue("engineer-r4", CannedResponse(action=hand_off("done", "All steps hold.")))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=0, require_independent_review=False),
        workdir=tmp_path,
    )

    assert _labels(backend) == ["engineer-r1", "engineer-r2", "engineer-r3", "engineer-r4"]
    assert status == "done"
    assert [record.review.review_source for record in rounds] == ["engineer_self_review"]


def test_a_round_capped_self_reviewed_mission_adds_no_reviewer_call(tmp_path: Path) -> None:
    """An explicit ``max_rounds`` is the operator's budget; it ends a
    self-reviewed mission without buying a Reviewer call."""
    backend = MemoryBackend()
    for index in (1, 2, 3, 4):
        backend.queue(f"engineer-r{index}", CannedResponse(action=hand_off("engineer", f"Step {index} written.")))

    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=False),
        workdir=tmp_path,
    )

    assert _labels(backend) == ["engineer-r1", "engineer-r2", "engineer-r3", "engineer-r4"]
    assert status == "max_rounds"
