"""Tests for the project garbage collector (argus_skill.core.project_gc).

Conservative + reversible: prune ONLY not-live AND stale projects, and
prune == move to ``projects_trash/`` (never rm). A running daemon/repl is
never touched; a recently-active project is never touched.
"""
from __future__ import annotations

import os
import time

from argus_skill.core.project_gc import gc_stale_projects, retention_days_default


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


def test_stale_unlocked_project_is_moved_to_trash(tmp_path):
    _make_project(tmp_path, "deadbeef0001", age_days=60)
    pruned = gc_stale_projects(tmp_path, retention_days=30)
    assert pruned == ["deadbeef0001"]
    # Moved, not deleted: original gone, copy lives under projects_trash/.
    assert not (tmp_path / "projects" / "deadbeef0001").exists()
    trash = list((tmp_path / "projects_trash").rglob("deadbeef0001"))
    assert trash and (trash[0] / "events.jsonl").exists()


def test_recent_project_is_kept(tmp_path):
    _make_project(tmp_path, "fresh00000001", age_days=1)
    assert gc_stale_projects(tmp_path, retention_days=30) == []
    assert (tmp_path / "projects" / "fresh00000001").exists()


def test_transcript_only_session_is_not_swept_as_empty(tmp_path):
    """A chat-only session (a saved conversation but no events/backlog) must NOT
    be trashed by the empty-sweep — that would delete the conversation history
    that /resume replays."""
    from argus_skill.core import transcript as T

    d = tmp_path / "projects" / "chatonly0001"
    d.mkdir(parents=True)
    (d / "session.json").write_text(
        '{"id":"chatonly0001","display_name":"","objective":""}', encoding="utf-8"
    )
    T.append_turn(d, "operator", "hello there")
    # Even after the empty-session grace, a transcript must be enough to keep
    # the session.
    assert gc_stale_projects(
        tmp_path,
        retention_days=30,
        sweep_empty=True,
        now=time.time() + 7200,
    ) == []
    assert (tmp_path / "projects" / "chatonly0001").exists()


def test_live_daemon_project_is_never_pruned(tmp_path):
    # Stale by mtime, but daemon.pid points at THIS (alive) process.
    _make_project(tmp_path, "live00000001", age_days=99, lock_pid=os.getpid())
    assert gc_stale_projects(tmp_path, retention_days=30) == []
    assert (tmp_path / "projects" / "live00000001").exists()


def test_dead_pid_lock_does_not_protect(tmp_path):
    # A stale lock pointing at a non-existent pid must NOT keep the project.
    _make_project(tmp_path, "dead00000001", age_days=99, lock_pid=2_000_000_000)
    assert gc_stale_projects(tmp_path, retention_days=30) == ["dead00000001"]


def test_dry_run_lists_but_does_not_move(tmp_path):
    _make_project(tmp_path, "stale00000001", age_days=60)
    pruned = gc_stale_projects(tmp_path, retention_days=30, dry_run=True)
    assert pruned == ["stale00000001"]
    assert (tmp_path / "projects" / "stale00000001").exists()  # untouched
    assert not (tmp_path / "projects_trash").exists()


def test_no_projects_root_is_noop(tmp_path):
    assert gc_stale_projects(tmp_path, retention_days=30) == []


def test_retention_env_override(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_RETENTION_DAYS", "7")
    assert retention_days_default() == 7
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_RETENTION_DAYS", raising=False)
    assert retention_days_default() == 30


def test_mixed_set(tmp_path):
    _make_project(tmp_path, "old0000000001", age_days=60)
    _make_project(tmp_path, "new0000000001", age_days=2)
    _make_project(tmp_path, "alive000000001", age_days=99, lock_pid=os.getpid())
    pruned = gc_stale_projects(tmp_path, retention_days=30)
    assert pruned == ["old0000000001"]
    assert (tmp_path / "projects" / "new0000000001").exists()
    assert (tmp_path / "projects" / "alive000000001").exists()


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
