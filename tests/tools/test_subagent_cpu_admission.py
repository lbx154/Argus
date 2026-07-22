from __future__ import annotations

import os

import pytest

from argus_skill.tools.subagent import _cpu_admission as cpu
from argus_skill.tools import subagent


def test_select_cpu_ids_skips_live_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu, "available_cpu_ids", lambda: (0, 1, 2, 3))
    tasks = [
        {"state": "running", "task_id": "a", "pid": 10, "cpu_ids": [0, 2]},
        {"state": "done", "task_id": "old", "pid": 11, "cpu_ids": [1]},
    ]

    selected = cpu.select_cpu_ids(
        cpu_count=2,
        tasks=tasks,
        is_pid_alive=lambda pid: pid == 10,
    )

    assert selected == (1, 3)


def test_select_cpu_ids_rejects_duplicate_explicit_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cpu, "available_cpu_ids", lambda: (0, 1, 2, 3))
    with pytest.raises(cpu.CpuAdmissionError, match="duplicate"):
        cpu.select_cpu_ids(
            cpu_ids="1,1",
            tasks=[],
            is_pid_alive=lambda _pid: False,
        )


def test_starting_placeholder_holds_lease_during_fork_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cpu, "available_cpu_ids", lambda: (0, 1))
    tasks = [{
        "state": "starting",
        "task_id": "a",
        "submitter_pid": 123,
        "submitted_at": 100.0,
        "cpu_ids": [0],
    }]

    assert cpu.leased_cpu_ids(
        tasks,
        is_pid_alive=lambda pid: pid == 123,
        now=101.0,
    ) == (0,)


def test_apply_current_process_affinity_verifies_exact_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {0, 1, 2, 3}
    applied: list[set[int]] = []

    def fake_setaffinity(pid: int, cpu_ids: set[int]) -> None:
        assert pid == 0
        current.clear()
        current.update(cpu_ids)
        applied.append(set(cpu_ids))

    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(current))
    monkeypatch.setattr(os, "sched_setaffinity", fake_setaffinity)

    cpu.apply_current_process_affinity((1, 3))

    assert applied == [{1, 3}]
    assert current == {1, 3}


def test_task_state_updates_preserve_cpu_lease_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    subagent._write_task(
        "job",
        {
            "state": "starting",
            "task_id": "job",
            "cpu_ids": [2, 3],
            "cpu_count": 2,
        },
    )
    subagent._write_task("job", {"state": "running", "task_id": "job", "pid": 10})

    record = subagent._read_task("job")

    assert record is not None
    assert record["cpu_ids"] == [2, 3]
    assert record["cpu_count"] == 2
