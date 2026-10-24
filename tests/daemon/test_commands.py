from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from argus.daemon.commands import (
    COMMAND_STATE_FILE,
    DaemonCommandStateError,
    claim_daemon_command,
    daemon_command_execution_lock,
    daemon_command_snapshot,
    execute_daemon_command,
    submit_daemon_command,
)


@pytest.mark.parametrize(
    ("operation", "args", "expected_revision"),
    [
        ("stop", {"resume": True}, 0),
        ("start", {"resume": False}, 0),
        ("start", {"resume": True}, 99),
    ],
)
def test_duplicate_command_id_rejects_a_different_request(
    tmp_path: Path,
    operation: str,
    args: dict,
    expected_revision: int,
) -> None:
    first = submit_daemon_command(
        tmp_path,
        operation="start",
        args={"resume": True},
        command_id="cmd-bound",
        expected_revision=0,
    )

    conflict = execute_daemon_command(
        tmp_path,
        operation=operation,
        args=args,
        handler=lambda: pytest.fail("conflicting request must not execute"),
        command_id="cmd-bound",
        expected_revision=expected_revision,
    )

    assert first.status == "accepted"
    assert conflict.status == "rejected"
    assert "command_id conflict" in conflict.error
    persisted = daemon_command_snapshot(tmp_path)["recent"][0]
    assert persisted["operation"] == "start"
    assert persisted["args"] == {"resume": True}


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


def test_execution_lock_releases_thread_lock_when_lock_file_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            "argus.daemon.commands.os.open",
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


def test_corrupt_command_state_fails_closed(tmp_path: Path) -> None:
    (tmp_path / COMMAND_STATE_FILE).write_text("{broken", encoding="utf-8")
    with pytest.raises(DaemonCommandStateError, match="cannot read command state"):
        submit_daemon_command(
            tmp_path,
            operation="start",
            command_id="cmd-1",
        )
