"""Tool steps streamed during a Manager turn are journaled with the reply."""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.webapi.manager_dispatch import (
    _TurnEmitter,
    finish_turn_steps,
    record_turn_step,
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
