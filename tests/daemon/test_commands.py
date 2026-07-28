from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from argus_skill.daemon.commands import (
    COMMAND_LOG_FILE,
    COMMAND_STATE_FILE,
    DaemonCommandStateError,
    claim_daemon_command,
    daemon_command_execution_lock,
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
    metrics = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert metrics[-1]["name"] == "daemon.command"
    assert metrics[-1]["labels"] == {"operation": "start", "status": "applied"}


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


def test_different_lifecycle_commands_execute_serially(tmp_path: Path) -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    receipts = []

    def first_handler():
        first_entered.set()
        release_first.wait(timeout=5)
        return {"rc": 0}

    def second_handler():
        second_entered.set()
        return {"rc": 0}

    first = threading.Thread(
        target=lambda: receipts.append(
            execute_daemon_command(
                tmp_path,
                operation="start",
                handler=first_handler,
                command_id="cmd-start-serial",
            )
        )
    )
    second = threading.Thread(
        target=lambda: receipts.append(
            execute_daemon_command(
                tmp_path,
                operation="stop",
                handler=second_handler,
                command_id="cmd-stop-serial",
            )
        )
    )
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert second_entered.is_set()
    assert [receipt.status for receipt in receipts] == ["applied", "applied"]


def test_blocking_execution_lock_has_bounded_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.daemon.commands._COMMAND_LOCK_TIMEOUT_SECONDS",
        0.02,
    )
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with daemon_command_execution_lock(tmp_path) as acquired:
            assert acquired
            entered.set()
            release.wait(timeout=2)

    owner = threading.Thread(target=hold_lock)
    owner.start()
    assert entered.wait(timeout=1)
    with daemon_command_execution_lock(tmp_path) as acquired:
        assert acquired is False
    release.set()
    owner.join(timeout=1)


def test_execution_lock_does_not_swallow_caller_timeout(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError, match="caller timed out"):
        with daemon_command_execution_lock(tmp_path) as acquired:
            assert acquired
            raise TimeoutError("caller timed out")


def test_execution_lock_releases_thread_lock_when_lock_file_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            "argus_skill.daemon.commands.os.open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                PermissionError("denied")
            ),
        )
        with pytest.raises(PermissionError, match="denied"):
            with daemon_command_execution_lock(tmp_path):
                pytest.fail("lock body must not run")

    with daemon_command_execution_lock(tmp_path, blocking=False) as acquired:
        assert acquired


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


def test_nonzero_handler_rc_is_persisted_as_failed(tmp_path: Path) -> None:
    failed = execute_daemon_command(
        tmp_path,
        operation="start",
        handler=lambda: {"rc": 2, "error": "daemon failed to start"},
        command_id="cmd-nonzero",
    )
    replayed = execute_daemon_command(
        tmp_path,
        operation="start",
        handler=lambda: {"rc": 0},
        command_id="cmd-nonzero",
    )

    assert failed.status == replayed.status == "failed"
    assert failed.result["rc"] == 2
    assert failed.error == "daemon failed to start"


def test_running_command_is_reclaimed_after_owner_process_dies(
    tmp_path: Path,
) -> None:
    submitted = submit_daemon_command(
        tmp_path,
        operation="start",
        command_id="cmd-orphaned",
    )
    assert claim_daemon_command(tmp_path, submitted.command_id)
    state_path = tmp_path / COMMAND_STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["commands"][submitted.command_id]["owner_pid"] = 2_000_000_000
    state_path.write_text(json.dumps(state), encoding="utf-8")
    calls: list[str] = []

    recovered = execute_daemon_command(
        tmp_path,
        operation="start",
        command_id=submitted.command_id,
        handler=lambda: calls.append("run") or {"rc": 0},
    )

    assert calls == ["run"]
    assert recovered.status == "applied"
    assert recovered.result == {"rc": 0}


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
