from __future__ import annotations

import json
from argparse import Namespace

from argus_skill.tools import subagent


def _status(task_id: str) -> int:
    return subagent.cmd_status(Namespace(task_id=task_id))


def test_running_status_exits_zero(tmp_path, monkeypatch, capsys):
    # A healthy running job must NOT read as a failed command.
    monkeypatch.setattr(subagent, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent, "_is_pid_alive", lambda pid: True)
    subagent._write_task("job1", {"state": "running", "task_id": "job1", "pid": 4321})
    rc = _status("job1")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["live"] is True


def test_failed_states_exit_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job2", {"state": "error", "task_id": "job2", "pid": 0})
    assert _status("job2") == 1
    capsys.readouterr()


def test_done_and_early_stopped_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job3", {"state": "done", "task_id": "job3", "pid": 0})
    subagent._write_task("job4", {"state": "early_stopped", "task_id": "job4", "pid": 0})
    assert _status("job3") == 0
    assert _status("job4") == 0
    capsys.readouterr()


def test_running_with_dead_pid_becomes_crashed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subagent, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(subagent, "_is_pid_alive", lambda pid: False)
    subagent._write_task("job5", {"state": "running", "task_id": "job5", "pid": 999999})
    rc = _status("job5")
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["state"] == "crashed"
