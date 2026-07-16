from __future__ import annotations

from types import SimpleNamespace

import argus_skill.daemon.process as process


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
