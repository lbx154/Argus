"""Tool steps streamed during a Manager turn are journaled with the reply."""

from __future__ import annotations

import json
from pathlib import Path

from argus.webapi.manager_dispatch import (
    _TurnEmitter,
    finish_turn_steps,
    record_turn_step,
    turn_step_event,
)


def _phase(**payload):
    return {"role": "manager", "label": payload.pop("label", "step"), **payload}


def test_tool_result_closes_the_step_that_started_the_same_call() -> None:
    steps: list[dict] = []
    record_turn_step(steps, _phase(label="$ wc -l README.md", kind="command_execution",
                                   tool="Count README lines", call_id="c1", status="running"), now=10.0)
    record_turn_step(steps, _phase(label="↳ Count README lines · completed", kind="tool_result",
                                   call_id="c1", status="completed", output="1 README.md"), now=12.5)
    assert len(steps) == 1
    assert steps[0]["status"] == "completed"
    assert steps[0]["ended_ts"] == 12.5
    assert steps[0]["output"] == "1 README.md"
    assert steps[0]["tool"] == "Count README lines"
    assert steps[0]["label"] == "$ wc -l README.md"


def test_a_new_call_closes_the_previous_open_step() -> None:
    steps: list[dict] = []
    record_turn_step(steps, _phase(label="⚙ view", kind="tool_use", call_id="c1"), now=1.0)
    record_turn_step(steps, _phase(label="⚙ rg", kind="tool_use", call_id="c2"), now=3.0)
    assert [s["status"] for s in steps] == ["completed", "running"]
    assert steps[0]["ended_ts"] == 3.0 and steps[1]["ended_ts"] == 0.0


def test_routing_narration_and_heartbeats_are_not_steps() -> None:
    steps: list[dict] = []
    record_turn_step(steps, _phase(label="Copilot handling it solo…"))
    record_turn_step(steps, _phase(label="waiting · 5s quiet", kind="tool_use", heartbeat=True))
    record_turn_step(steps, _phase(label="↳ orphan result", kind="tool_result", call_id="missing"))
    assert steps == []


def test_steps_are_bounded_and_clipped() -> None:
    steps: list[dict] = []
    for index in range(200):
        record_turn_step(steps, _phase(label="x" * 1000, kind="tool_use", call_id=f"c{index}"), now=float(index))
    assert len(steps) == 80
    assert len(steps[0]["label"]) == 240


def test_finish_closes_open_steps() -> None:
    steps = [{"kind": "tool_use", "label": "⚙ view", "started_ts": 1.0, "ended_ts": 0.0, "status": "running"}]
    finish_turn_steps(steps, now=4.0)
    assert steps[0] == {"kind": "tool_use", "label": "⚙ view", "started_ts": 1.0, "ended_ts": 4.0, "status": "completed"}


def test_failed_turn_does_not_complete_unfinished_tools() -> None:
    steps = [
        {"label": "read", "status": "completed", "ended_ts": 2.0},
        {"label": "write", "status": "running", "ended_ts": 0.0},
    ]
    finish_turn_steps(steps, now=4.0, failed=True)
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "interrupted"
    assert steps[1]["ended_ts"] == 4.0


def test_journal_writes_steps_to_transcript_and_live_event(tmp_path: Path) -> None:
    fragments: list[tuple[str, dict]] = []
    emitter = _TurnEmitter(
        life_dir=tmp_path,
        turn_id="web-1",
        fragment=lambda kind, payload: fragments.append((kind, payload)),
    )
    record_turn_step(emitter.steps, _phase(label="$ ls", kind="command_execution", call_id="c1"), now=1.0)
    result = emitter.respond("one file", {"kind": "chat"})

    assert result["steps"][0]["label"] == "$ ls"
    assert result["steps"][0]["status"] == "completed"
    transcript = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    assert transcript[-1]["role"] == "argus"
    assert transcript[-1]["steps"][0]["call_id"] == "c1"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    argus = [event for event in events if event.get("type") == "ui.argus"]
    assert argus and argus[-1]["steps"][0]["label"] == "$ ls"
    assert fragments == [("delta", {"text": "one file", "fragment_mode": "snapshot"})]


def test_a_turn_without_tools_journals_no_steps(tmp_path: Path) -> None:
    emitter = _TurnEmitter(life_dir=tmp_path, turn_id="web-2", fragment=lambda *_: None)
    result = emitter.respond("hello", {"kind": "chat"})
    assert "steps" not in result
    transcript = json.loads((tmp_path / "transcript.jsonl").read_text().splitlines()[-1])
    assert "steps" not in transcript


def test_each_frame_returns_the_step_it_opened_or_closed_and_its_durable_twin() -> None:
    steps: list[dict] = []
    [opened] = record_turn_step(steps, _phase(label="$ wc -l README.md", kind="command_execution",
                                              tool="Count README lines", call_id="c1", status="running"), now=10.0)
    assert opened is steps[0]
    assert record_turn_step(steps, {"role": "manager", "label": "thinking…", "heartbeat": True}) == []
    assert record_turn_step(steps, _phase(label="handling it solo", kind="routing")) == []
    started = turn_step_event("web-7", opened, now=10.0)
    assert started == {
        "type": "engineer.progress", "kind": "command_execution",
        "agent_layer": "manager", "actor": "manager",
        "item_id": "turn:web-7", "message_id": "web-7:c1",
        "text": "$ wc -l README.md", "action_summary": "$ wc -l README.md",
        "status": "running", "turn_step": True, "ts": 10.0,
        "tool_name": "Count README lines", "call_id": "c1",
    }
    [closed] = record_turn_step(steps, _phase(label="↳ Count README lines · completed", kind="tool_result",
                                              call_id="c1", status="completed", output="1 README.md"), now=12.5)
    assert closed is opened
    finished = turn_step_event("web-7", closed, now=12.5)
    # Same message id: the mission view updates the one work record in place.
    assert finished["message_id"] == started["message_id"]
    assert finished["status"] == "completed"
    assert finished["action_summary"] == "$ wc -l README.md · 1 README.md"
    assert finished["text"] == "$ wc -l README.md"


def test_emitter_persists_steps_only_once_the_turn_has_a_card(tmp_path: Path) -> None:
    life = tmp_path / "life"
    life.mkdir()
    emitter = _TurnEmitter(life_dir=life, turn_id="web-9", fragment=lambda _kind, _payload: None,
                           task_objective="count it")
    step = {"kind": "tool_use", "label": "⚙ view", "call_id": "v1", "status": "running", "started_ts": 1.0, "ended_ts": 0.0}
    emitter.record_steps([step])
    assert not (life / "events.jsonl").exists()
    emitter.solo = True
    emitter.start_task()
    emitter.record_steps([step])
    emitter.record_steps([])
    rows = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    assert [row["type"] for row in rows] == ["manager.turn.started", "engineer.progress"]
    assert rows[1]["item_id"] == "turn:web-9" and rows[1]["turn_step"] is True


def test_a_new_call_reports_the_step_it_closed_and_a_nameless_result_closes_the_open_one() -> None:
    steps: list[dict] = []
    [first] = record_turn_step(steps, _phase(label="$ date", kind="command_execution", tool="bash"), now=1.0)
    # Backends that name no call still end one call before the next begins.
    [ended] = record_turn_step(steps, _phase(label="↳ bash · completed", kind="tool_result",
                                             status="completed", output="Tue"), now=2.0)
    assert ended is first and first["status"] == "completed" and first["output"] == "Tue"
    [second] = record_turn_step(steps, _phase(label="$ ls", kind="command_execution", tool="bash"), now=3.0)
    changed = record_turn_step(steps, _phase(label="⚙ view", kind="tool_use", call_id="v1"), now=4.0)
    assert changed[0] is second and second["status"] == "completed" and second["ended_ts"] == 4.0
    assert changed[1] is steps[-1] and steps[-1]["status"] == "running"
    # The two nameless steps get distinct durable ids from their start times.
    assert turn_step_event("web-1", first)["message_id"] != turn_step_event("web-1", second)["message_id"]
    # An orphan result that names an unknown call is still ignored.
    assert record_turn_step(steps, _phase(label="↳ orphan", kind="tool_result", call_id="missing"), now=5.0) == []
