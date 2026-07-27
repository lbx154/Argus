"""Team completion must reach the operator without a follow-up question."""

from __future__ import annotations

import json

from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Runner:
    pass


def test_continuous_mission_completion_publishes_once(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="keep improving",
            open_ended=True,
        ),
    )
    event = {
        "type": "life.mission.completed",
        "item_id": "task-1",
        "title": "Fix the first issue",
        "success": True,
        "status": "done",
        "outcome": {"review_status": "done"},
    }

    assert supervisor._emit(event) is True
    assert supervisor._emit(event) is True

    transcript = [
        json.loads(line)
        for line in (tmp_path / "transcript.jsonl").read_text().splitlines()
    ]
    assert len(transcript) == 1
    assert transcript[0]["role"] == "argus"
    assert "Team completed · Fix the first issue · review=done" in transcript[0]["text"]
    assert "Planner is selecting the next task" in transcript[0]["text"]
    ui_events = [
        event
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if (event := json.loads(line)).get("type") == "ui.argus"
    ]
    assert len(ui_events) == 1


def test_bounded_completion_says_the_task_is_finished(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )

    supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-2",
        "title": "One bounded fix",
        "success": True,
        "status": "done",
        "outcome": {"review_status": "not_assessed"},
    })

    text = json.loads(
        (tmp_path / "transcript.jsonl").read_text().splitlines()[-1]
    )["text"]
    assert text == "Team completed · One bounded fix\nThis task is finished."
