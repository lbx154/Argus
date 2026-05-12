"""Regression tests for the ``argus-skill --status`` command."""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps.cli import _cmd_status
from argus_skill.life import BacklogItem, MemoryBundle
from argus_skill.life.memory import JournalEntry


@pytest.fixture()
def project_with_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
    mem.global_mem.journal.append(
        JournalEntry.new(kind="mission_failed", title="old failure", summary="boom")
    )
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    return home, repo


@pytest.fixture()
def project_with_active_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
    mem.global_mem.journal.append(
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
    return home, repo


def test_status_separates_active_queue_from_history(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root

    assert rc == 0
    assert "active   : 0 pending · 0 running" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert str(project_root) in out
    assert "done   :" not in out
    assert "failed" in out
    assert "continuous: off" in out


def test_status_shows_active_work_when_present(
    monkeypatch: pytest.MonkeyPatch,
    project_with_active_and_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_active_and_history
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root

    assert rc == 0
    assert "active   : 1 pending · 1 running" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert str(project_root) in out
    assert "pending" in out
    assert "running" in out
