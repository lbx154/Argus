"""Tool steps streamed during a Manager turn are journaled with the reply."""

from __future__ import annotations

from argus.webapi.manager_dispatch import (
    record_turn_step,
    turn_step_event,
)


def _phase(**payload):
    return {"role": "manager", "label": payload.pop("label", "step"), **payload}


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
