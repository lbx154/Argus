from __future__ import annotations

from types import SimpleNamespace

import argus_skill.daemon.process as process


def _status(*, alive: bool, pid: int | None = None):
    return SimpleNamespace(
        alive=alive,
        pid=pid,
        status_read_error="",
    )


def test_startup_stability_rejects_daemon_that_dies_after_pid_publication(
    tmp_path,
    monkeypatch,
) -> None:
    clock = [0.0]
    statuses = iter([
        _status(alive=False),
        _status(alive=True, pid=42),
        _status(alive=True, pid=42),
        _status(alive=False),
    ])
    last = _status(alive=False)
    monkeypatch.setattr(
        process,
        "read_daemon_status",
        lambda _path: next(statuses, last),
    )
    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        process.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    result = process._wait_for_stable_daemon_status(
        tmp_path,
        publish_timeout_s=2.0,
        stable_for_s=0.5,
        poll_interval_s=0.1,
    )

    assert result is None


def test_startup_stability_accepts_continuously_live_daemon(
    tmp_path,
    monkeypatch,
) -> None:
    clock = [0.0]
    stable = _status(alive=True, pid=42)
    monkeypatch.setattr(process, "read_daemon_status", lambda _path: stable)
    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        process.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    result = process._wait_for_stable_daemon_status(
        tmp_path,
        publish_timeout_s=2.0,
        stable_for_s=0.5,
        poll_interval_s=0.1,
    )

    assert result is stable


def test_spawn_rejects_workspace_before_fork(tmp_path, monkeypatch) -> None:
    config = SimpleNamespace(life_dir=tmp_path / "life")
    released: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(
        process,
        "read_daemon_status",
        lambda _path: SimpleNamespace(alive=False, pid=None),
    )
    monkeypatch.setattr(
        process.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("must not fork")),
        raising=False,
    )

    rc = process.spawn_detached_process(
        config,
        worker_factory=lambda _config: None,
        acquire_spawn_lock=lambda _config: 7,
        release_spawn_lock=lambda fd, unlock=True: released.append((fd, unlock)),
        max_active_daemons=lambda _config: 2,
        active_daemon_count=lambda _config: 0,
        workspace_start_error=lambda _config: "workdir already owned",
        quiet=True,
    )

    assert rc == 3
    assert released == [(7, True)]


def test_close_inherited_fds_preserves_workspace_lease_without_proc(
    monkeypatch,
) -> None:
    closed_ranges: list[tuple[int, int]] = []
    monkeypatch.setattr(
        process.os,
        "listdir",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError),
    )
    monkeypatch.setattr(
        process.os,
        "closerange",
        lambda low, high: closed_ranges.append((low, high)),
    )

    process._close_inherited_fds(7)

    assert closed_ranges == [(3, 7), (8, 4096)]
