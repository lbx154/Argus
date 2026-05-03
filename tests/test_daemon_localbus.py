"""Integration test: daemon + LocalBus channel + memory backend.

Drives the daemon end-to-end without any network or LLM:

  * Construct a SkillLoop with a MemoryBackend.
  * Wrap it in a Daemon with JsonlEventSink (outbox.jsonl).
  * Connect a LocalBusControlChannel reading from inbox.jsonl.
  * Publish ``run`` / ``inject`` / ``stop`` to the bus.
  * Wait for the daemon to drain, then assert events appeared in the
    outbox and the status file shows the right counts.

Validates the four 24/7 properties: command dispatch, status reporting,
inject buffering, graceful stop.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.control_channels import LocalBusControlChannel
from argus_skill.adapters.event_sinks import (
    CompositeEventSink,
    JsonlEventSink,
    TerminalEventSink,
)
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.daemon.bus import (
    BusCommand,
    JsonlCommandBus,
    read_status,
)
from argus_skill.daemon.runtime import Daemon, DaemonConfig

SKILL_MD = (
    "## Title\nDaemon demo capability\n\n"
    "## Description\nA capability for demoing daemon ops.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- daemon smoke tests\n\n"
    "## When NOT to use\n- production work\n\n"
    "## How to solve\n- Read the task.\n- Reply concisely.\n\n"
    "## Examples\n- (none)\n\n"
    "## Response shape\n- Reply inline.\n"
)


def _done_review(reason: str = "ok") -> str:
    return json.dumps({
        "status": "done",
        "confidence": 0.9,
        "reason": reason,
        "next_action": "No further action needed.",
        "round_summary_markdown": "# review\n\n- ok\n",
        "completion_summary_markdown": "Done.",
    })


def _build_backend_for_two_runs() -> MemoryBackend:
    backend = MemoryBackend()
    # Run 1.
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(
        message="Done: replied to first task. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review("first done")))
    # Run 2.
    backend.queue("matcher", CannedResponse(message=json.dumps({
        "matched": [{"name": "Daemon demo capability", "fit": "high", "why": "demo"}],
    })))
    backend.queue("engineer-r1", CannedResponse(
        message="Done: replied to second task with operator hint. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review("second done")))
    return backend


def _wait_for(predicate, *, timeout=8.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _read_outbox_events(path: Path) -> list[dict]:
    events: list[dict] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        evt = payload.get("event")
        if isinstance(evt, dict):
            events.append(evt)
    return events


def test_daemon_runs_two_tasks_with_inject_and_stop(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    inbox = state_dir / "inbox.jsonl"
    outbox = state_dir / "outbox.jsonl"
    status = state_dir / "status.json"

    backend = _build_backend_for_two_runs()
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        scientist_runner=backend,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=3, skill_writeback=False),
    )
    sinks = CompositeEventSink([
        TerminalEventSink(verbose=False),
        JsonlEventSink(str(outbox)),
    ])
    daemon = Daemon(loop=loop, sinks=sinks, config=DaemonConfig(
        status_path=str(status),
        status_refresh_seconds=1,
        workdir=str(tmp_path),
    ))
    channel = LocalBusControlChannel(path=str(inbox), source="local-bus")
    channel.start(daemon.handle_command)
    daemon.start()
    try:
        bus = JsonlCommandBus(str(inbox))
        bus.publish(BusCommand(kind="run", text="first task", source="test", ts=time.time()))

        # Wait for first task to finish before queuing the second.
        assert _wait_for(
            lambda: daemon.state.tasks_done >= 1,
            timeout=15.0,
        ), "first task did not complete"

        # Buffer an inject for the next /run, then queue it.
        bus.publish(BusCommand(kind="inject", text="prefer concise output", source="test", ts=time.time()))
        bus.publish(BusCommand(kind="run", text="second task", source="test", ts=time.time()))

        assert _wait_for(
            lambda: daemon.state.tasks_done >= 2,
            timeout=15.0,
        ), "second task did not complete"

        bus.publish(BusCommand(kind="stop", text="", source="test", ts=time.time()))

        assert _wait_for(lambda: not (daemon._worker is not None  # noqa: SLF001
                                      and daemon._worker.is_alive()),
                         timeout=15.0), "daemon worker did not exit after /stop"
    finally:
        channel.stop()
        daemon.stop(timeout=5.0)

    events = _read_outbox_events(outbox)
    event_types = [e.get("type") for e in events]
    # Both runs should have completed (or at minimum: started).
    assert event_types.count("task.started") >= 2
    assert event_types.count("task.completed") >= 2
    assert "daemon.started" in event_types
    assert "daemon.stopping" in event_types

    # Inject should have been delivered to engineer-r1 of the second run.
    second_run_engineer_prompts = [
        prompt
        for label, prompt, _ in backend.history
        if label == "engineer-r1"
    ]
    # We have two "engineer-r1" calls — one per run. The second one should
    # contain the operator inject text because /inject was buffered before
    # the second /run.
    assert len(second_run_engineer_prompts) == 2
    assert "prefer concise output" in second_run_engineer_prompts[1], (
        "second run's engineer prompt should include the buffered inject"
    )

    # Status file should show two runs done.
    final_status = read_status(str(status))
    assert final_status.get("tasks_run") == 2
    assert final_status.get("tasks_done") == 2
