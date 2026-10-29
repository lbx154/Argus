"""A workspace whose storage is full pauses its work and says so.

The hosted runtime's storage monitor (trial_server/deploy/native_runtime.py)
records the pause in the Argus home; the server refuses new daemons, keeps
the work a host shutdown interrupted for later, and puts the numbers on the
project page (2026-10-29 trial4: the runtime stopped for 12 hours and the page
showed nothing).
"""
from __future__ import annotations

from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.core.storage_pause import clear_storage_pause, record_storage_pause
from argus.daemon import life_worker as daemon_worker
from argus.daemon.state import (
    read_continuous_state,
    read_host_shutdown,
    record_host_shutdown,
    write_continuous_config,
)
from argus.webapi import daemon_lifecycle, host_resume, project_state

GB = 1024**3


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return life


def test_no_daemon_starts_while_storage_is_full(tmp_path, monkeypatch):
    _project(tmp_path, "s-full")
    spawned = []
    monkeypatch.setattr(
        daemon_worker, "spawn_detached_daemon_clean",
        lambda config, quiet=True: spawned.append(config.life_dir) or 0,
    )
    record_storage_pause(tmp_path, used_bytes=34 * GB, limit_bytes=30 * GB)

    result = daemon_lifecycle.start_project_daemon("s-full", global_root=tmp_path)

    assert result is not None and result["rc"] == 4
    assert result["error"].startswith("workspace storage is full (34.0 GB of 30.0 GB)")
    assert spawned == []


def test_the_project_page_carries_the_pause(tmp_path):
    _project(tmp_path, "s-page")
    assert project_state.build_snapshot("s-page", global_root=tmp_path)["storage_pause"] is None
    record_storage_pause(tmp_path, used_bytes=34 * GB, limit_bytes=30 * GB)

    pause = project_state.build_snapshot("s-page", global_root=tmp_path)["storage_pause"]

    assert pause["used_bytes"] == 34 * GB and pause["limit_bytes"] == 30 * GB


def test_interrupted_work_waits_for_room_then_resumes(tmp_path):
    life = _project(tmp_path, "s-interrupted")
    write_continuous_config(life, enabled=True, objective="train the model")
    generation = read_continuous_state(life).generation
    record_host_shutdown(life, pid=1, started_at_iso="2026-10-29T00:00:00", signum=15, campaign_generation=generation)
    started = []

    def start(sid, **_kwargs):
        started.append(sid)
        return {"rc": 0}

    record_storage_pause(tmp_path, used_bytes=34 * GB, limit_bytes=30 * GB)
    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == []
    assert read_host_shutdown(life) is not None

    clear_storage_pause(tmp_path)
    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == ["s-interrupted"]
