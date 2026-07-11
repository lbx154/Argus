from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from argus_skill.daemon.commands import (
    COMMAND_LOG_FILE,
    COMMAND_STATE_FILE,
    DaemonCommandStateError,
    daemon_command_snapshot,
    execute_daemon_command,
    submit_daemon_command,
)


def test_duplicate_command_id_executes_handler_exactly_once(tmp_path: Path) -> None:
    calls = []

    def handler():
        calls.append("run")
        return {"rc": 0, "daemon": {"alive": True}}

    first = execute_daemon_command(
        tmp_path,
        operation="start",
        handler=handler,
        command_id="cmd-1",
        expected_revision=0,
    )
    duplicate = execute_daemon_command(
        tmp_path,
        operation="start",
        handler=handler,
        command_id="cmd-1",
        expected_revision=0,
    )

    assert calls == ["run"]
    assert first.status == duplicate.status == "applied"
    assert duplicate.result["rc"] == 0
    assert duplicate.revision == first.revision
    assert daemon_command_snapshot(tmp_path)["revision"] == 3


def test_stale_expected_revision_is_durably_rejected(tmp_path: Path) -> None:
    first = submit_daemon_command(
        tmp_path,
        operation="start",
        command_id="cmd-1",
        expected_revision=0,
    )
    assert first.status == "accepted"

    stale = execute_daemon_command(
        tmp_path,
        operation="stop",
        handler=lambda: pytest.fail("stale command must not execute"),
        command_id="cmd-2",
        expected_revision=0,
    )

    assert stale.status == "rejected"
    assert "stale command revision" in stale.error
    snapshot = daemon_command_snapshot(tmp_path)
    assert snapshot["revision"] == 2
    assert snapshot["recent"][0]["command_id"] == "cmd-2"


def test_concurrent_duplicate_has_one_claimant(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = []
    receipts = []

    def handler():
        calls.append("run")
        entered.set()
        release.wait(timeout=5)
        return {"rc": 0}

    first = threading.Thread(
        target=lambda: receipts.append(execute_daemon_command(
            tmp_path,
            operation="drain",
            handler=handler,
            command_id="cmd-concurrent",
        ))
    )
    first.start()
    assert entered.wait(timeout=5)
    duplicate = execute_daemon_command(
        tmp_path,
        operation="drain",
        handler=handler,
        command_id="cmd-concurrent",
    )
    assert duplicate.status == "running"
    release.set()
    first.join(timeout=5)

    assert calls == ["run"]
    assert receipts[0].status == "applied"


def test_handler_failure_is_persisted_and_replayed(tmp_path: Path) -> None:
    def broken():
        raise RuntimeError("cannot signal daemon")

    failed = execute_daemon_command(
        tmp_path,
        operation="kill",
        handler=broken,
        command_id="cmd-fail",
    )
    replayed = execute_daemon_command(
        tmp_path,
        operation="kill",
        handler=lambda: {"rc": 0},
        command_id="cmd-fail",
    )
    assert failed.status == replayed.status == "failed"
    assert "cannot signal daemon" in replayed.error


def test_command_log_and_events_are_versioned(tmp_path: Path) -> None:
    receipt = execute_daemon_command(
        tmp_path,
        operation="replace",
        handler=lambda: {"rc": 0, "parked_session": "s-old"},
        args={"victim_sid": "s-old"},
        command_id="cmd-events",
    )
    assert receipt.status == "applied"
    commands = [
        json.loads(line)
        for line in (tmp_path / COMMAND_LOG_FILE).read_text().splitlines()
    ]
    assert len(commands) == 1
    assert commands[0]["command_id"] == "cmd-events"
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    assert [event["type"] for event in events] == [
        "daemon.command.submitted",
        "daemon.command.completed",
    ]
    assert all("event_validation" not in event for event in events)


def test_corrupt_command_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / COMMAND_STATE_FILE).write_text("{broken", encoding="utf-8")
    with pytest.raises(DaemonCommandStateError, match="cannot read command state"):
        submit_daemon_command(
            tmp_path,
            operation="start",
            command_id="cmd-1",
        )
