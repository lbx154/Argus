"""Tests for the project garbage collector (argus.core.project_gc).

Conservative + reversible: prune ONLY not-live AND stale projects, and
prune == move to ``projects_trash/`` (never rm). A running daemon/repl is
never touched; a recently-active project is never touched.
"""
from __future__ import annotations

import os
import time

from argus.core.project_gc import gc_stale_projects, retention_days_default


def _make_project(root, name, *, age_days=0.0, lock_pid=None):
    """Create projects/<name>/ with optional staleness + a lock pid file."""
    d = root / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    if lock_pid is not None:
        (d / "daemon.pid").write_text(f"{lock_pid}\n", encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400.0
        for p in (d / "events.jsonl", d):
            os.utime(p, (old, old))
    return d


def test_live_daemon_project_is_never_pruned(tmp_path):
    # Stale by mtime, but daemon.pid points at THIS (alive) process.
    _make_project(tmp_path, "live00000001", age_days=99, lock_pid=os.getpid())
    assert gc_stale_projects(tmp_path, retention_days=30) == []
    assert (tmp_path / "projects" / "live00000001").exists()


def test_dry_run_lists_but_does_not_move(tmp_path):
    _make_project(tmp_path, "stale00000001", age_days=60)
    pruned = gc_stale_projects(tmp_path, retention_days=30, dry_run=True)
    assert pruned == ["stale00000001"]
    assert (tmp_path / "projects" / "stale00000001").exists()  # untouched
    assert not (tmp_path / "projects_trash").exists()


def test_retention_env_override(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_RETENTION_DAYS", "7")
    assert retention_days_default() == 7
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_RETENTION_DAYS", raising=False)
    assert retention_days_default() == 30


def test_excluded_session_is_never_pruned(tmp_path):
    # The resume-GC data-loss guard: a stale, not-live project that is the
    # caller's OWN just-resolved session must survive the startup sweep (it is
    # not-yet-locked), even though by age it would otherwise be trashed.
    _make_project(tmp_path, "resuming00001", age_days=99)
    _make_project(tmp_path, "other00000001", age_days=99)
    pruned = gc_stale_projects(tmp_path, retention_days=30, exclude={"resuming00001"})
    assert pruned == ["other00000001"]                                  # the other stale one goes
    assert (tmp_path / "projects" / "resuming00001").exists()           # the resumed one survives
    assert not (tmp_path / "projects" / "other00000001").exists()


def test_a_negative_retention_window_is_refused(tmp_path):
    """`--gc-days -5` for `--gc-days 5` used to trash every project.

    A negative window puts the cutoff in the future, so every project reads as
    untouched for longer than it — including one modified a second ago. The
    env spelling was already clamped by ``retention_days_default``; the
    explicit argument was not, and this is the destructive path.

    Measured on a real global root before the guard: 50 projects moved to
    trash, 42 of which the same run's ``--gc-dry-run`` had not listed.
    """
    import pytest

    _make_project(tmp_path, "fresh", age_days=0.0)

    with pytest.raises(ValueError, match="must not be negative"):
        gc_stale_projects(tmp_path, retention_days=-5)

    assert (tmp_path / "projects" / "fresh").is_dir()
    assert not (tmp_path / "projects_trash").exists()
