from __future__ import annotations

import json
import os
from argparse import Namespace

import pytest

from argus_skill.tools import subagent


def _status(task_id: str) -> int:
    return subagent.cmd_status(Namespace(task_id=task_id))


def test_running_status_exits_zero(tmp_path, monkeypatch, capsys):
    # A healthy running job must NOT read as a failed command.
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(subagent._cli, "_is_pid_alive", lambda pid: True)
    subagent._write_task("job1", {"state": "running", "task_id": "job1", "pid": 4321})
    rc = _status("job1")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["live"] is True


def test_failed_states_exit_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(subagent._cli, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job2", {"state": "error", "task_id": "job2", "pid": 0})
    assert _status("job2") == 1
    capsys.readouterr()


def test_done_and_early_stopped_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(subagent._cli, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job3", {"state": "done", "task_id": "job3", "pid": 0})
    subagent._write_task("job4", {"state": "early_stopped", "task_id": "job4", "pid": 0})
    assert _status("job3") == 0
    assert _status("job4") == 0
    capsys.readouterr()


def test_running_with_dead_pid_becomes_crashed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(subagent._cli, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job5", {"state": "running", "task_id": "job5", "pid": 999999})
    rc = _status("job5")
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["state"] == "crashed"


def test_reconcile_ignores_exit_sidecar_from_previous_run(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: False)
    old_sidecar = subagent._registry._exit_status_path("job6", "old-run")
    old_sidecar.parent.mkdir(parents=True)
    old_sidecar.write_text("0\n")
    task = {
        "state": "running",
        "task_id": "job6",
        "run_id": "new-run",
        "pid": 999999,
    }
    subagent._write_task("job6", task)

    reconciled = subagent._registry.reconcile_terminal_task("job6", task)

    assert reconciled["state"] == "crashed"
    assert "no exit sidecar" in reconciled["error"]


def test_reconcile_prefers_current_exit_sidecar_over_live_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent._registry, "_is_pid_alive", lambda pid: True)
    sidecar = subagent._registry._exit_status_path("job7", "current-run")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("0\n", encoding="utf-8")
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    stdout.write_text("survived", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    task = {
        "state": "running",
        "task_id": "job7",
        "run_id": "current-run",
        "pid": 4321,
        "worker_pid": 4322,
        "stdout_log": str(stdout),
        "stderr_log": str(stderr),
    }
    subagent._write_task("job7", task)

    reconciled = subagent._registry.reconcile_terminal_task("job7", task)

    assert reconciled["state"] == "done"
    assert reconciled["exit_code"] == 0
    assert reconciled["terminal_owner"] == "exit_sidecar_reconciler"
    assert reconciled["stdout_tail"] == "survived"


def test_reconcile_rejects_reused_live_pid_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(subagent._registry, "REGISTRY_DIR", tmp_path)
    identity = subagent._registry._process_identity(os.getpid())
    if "start_time_ticks" not in identity:
        pytest.skip("requires /proc process start-time identity")
    identity["start_time_ticks"] = f"{identity['start_time_ticks']}-reused"
    task = {
        "state": "running",
        "task_id": "reused-pid",
        "run_id": "run-1",
        "pid": os.getpid(),
        "process_identity": identity,
    }
    subagent._write_task("reused-pid", task)

    reconciled = subagent._registry.reconcile_terminal_task("reused-pid", task)

    assert reconciled["state"] == "crashed"
    assert "no exit sidecar" in reconciled["error"]
