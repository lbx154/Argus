from __future__ import annotations

import os
import time
from pathlib import Path

from argus_skill.tools import subagent as sa


def _park(task_id: str) -> None:
    # A parked discussion the predicate will treat as live: this very test
    # process is the worker_pid (definitely alive) with a fresh heartbeat.
    sa._write_task(task_id, {
        "task_id": task_id,
        "state": "discussing",
        "worker_pid": os.getpid(),
        "last_heartbeat": time.time(),
    })


def test_lane_of_parsing() -> None:
    assert sa._lane_of("t1::w1") == "t1"
    assert sa._lane_of("plain-task") is None
    assert sa._lane_of(None) is None


def test_lane_scopes_discussion_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)   # REGISTRY_DIR is relative to cwd
    _park("t1::w1")
    # same lane -> blocked
    assert [b["task_id"] for b in sa._open_discussion_blockers(lane="t1")] == ["t1::w1"]
    # different lane -> NOT blocked (no cross-team deadlock)
    assert sa._open_discussion_blockers(lane="t2") == []
    # legacy / no-lane caller -> sees all (global back-compat)
    assert [b["task_id"] for b in sa._open_discussion_blockers(lane=None)] == ["t1::w1"]


def test_legacy_taskid_still_globally_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _park("plain-legacy")
    # a legacy (no-lane) parked task blocks a legacy submit (lane None)
    assert [b["task_id"] for b in sa._open_discussion_blockers(lane=None)] == ["plain-legacy"]
