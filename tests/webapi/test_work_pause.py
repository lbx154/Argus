"""A service pause stops starts and automatic recovery, and is exposed unchanged."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core import hosting
from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.daemon.state import (
    read_continuous_state,
    read_host_shutdown,
    record_host_shutdown,
    write_continuous_config,
)
from argus.webapi import daemon_lifecycle, host_resume, project_state


@pytest.fixture
def set_pause(monkeypatch):
    current = None
    provider = SimpleNamespace(work_pause=lambda root: current)
    monkeypatch.setattr(hosting, "hosted_runtime", lambda: provider)

    def set_value(value):
        nonlocal current
        current = value

    return set_value


PAUSE = {"reason": "Workspace maintenance in progress", "since": 1793318400.0}


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return life


def test_no_daemon_starts_while_work_is_paused(tmp_path, monkeypatch, set_pause):
    _project(tmp_path, "s-full")
    spawned = []
    monkeypatch.setattr(
        daemon_worker, "spawn_detached_daemon_clean",
        lambda config, quiet=True: spawned.append(config.life_dir) or 0,
    )
    set_pause(PAUSE)

    result = daemon_lifecycle.start_project_daemon("s-full", global_root=tmp_path)

    assert result is not None and result["rc"] == 4
    assert result["error"] == PAUSE["reason"]
    assert result["work_pause"] == PAUSE
    assert spawned == []


def test_the_project_page_carries_the_pause(tmp_path, set_pause):
    _project(tmp_path, "s-page")
    assert project_state.build_snapshot("s-page", global_root=tmp_path)["work_pause"] is None
    set_pause(PAUSE)

    pause = project_state.build_snapshot("s-page", global_root=tmp_path)["work_pause"]

    assert pause == PAUSE


def test_interrupted_work_waits_for_clearance_then_resumes(tmp_path, set_pause):
    life = _project(tmp_path, "s-interrupted")
    write_continuous_config(life, enabled=True, objective="train the model")
    generation = read_continuous_state(life).generation
    record_host_shutdown(life, pid=1, started_at_iso="2026-10-29T00:00:00", signum=15, campaign_generation=generation)
    started = []

    def start(sid, **_kwargs):
        started.append(sid)
        return {"rc": 0}

    set_pause(PAUSE)
    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == []
    assert read_host_shutdown(life) is not None

    set_pause(None)
    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == ["s-interrupted"]
