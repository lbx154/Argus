"""Regression tests for the ``argus-skill --status`` command."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps.cli import _cmd_status
from argus_skill.life.memory import BacklogItem, JournalEntry, LifeMemory


@pytest.fixture()
def life_dir_with_history(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.journal.append(
        JournalEntry.new(kind="mission_failed", title="old failure", summary="boom")
    )
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    return tmp_path


@pytest.fixture()
def life_dir_with_active_and_history(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.journal.append(
        JournalEntry.new(kind="mission_failed", title="old failure", summary="boom")
    )
    pending = mem.backlog.add(BacklogItem.new(title="pending", objective="queued work"))
    running = mem.backlog.add(BacklogItem.new(title="running", objective="in flight"))
    mem.backlog.mark_running(running.id)
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    assert pending.id
    return tmp_path


def test_status_separates_active_queue_from_history(
    monkeypatch: pytest.MonkeyPatch,
    life_dir_with_history: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("argus_skill.daemon.life_worker.read_daemon_status", lambda life_dir: Namespace(
        alive=True,
        pid=4321,
        uptime_seconds=12.0,
        backend="memory",
    ))
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    (life_dir_with_history / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "objective": "hardening objective",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    rc = _cmd_status(Namespace(life_dir=str(life_dir_with_history)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "active   : 0 pending · 0 running" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert "done   :" not in out
    assert "failed" in out
    assert "continuous: on" in out
    assert "objective: hardening objective" in out
    assert "done_reason: planner declared project done" in out
    assert "done_at: 2026-05-12T00:00:00Z" in out


def test_status_shows_active_work_when_present(
    monkeypatch: pytest.MonkeyPatch,
    life_dir_with_active_and_history: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("argus_skill.daemon.life_worker.read_daemon_status", lambda life_dir: Namespace(
        alive=True,
        pid=4321,
        uptime_seconds=12.0,
        backend="memory",
    ))
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_dir_with_active_and_history)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "active   : 1 pending · 1 running" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert "pending" in out
    assert "running" in out
