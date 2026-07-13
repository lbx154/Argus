"""UX-C: hide empty-session litter from the picker + GC sweeps it safely.

Every bare ``argus-skill`` launch mints a fresh session dir; they piled up to 69
empty shells that made the resume picker useless. list_sessions(include_empty=
False) hides content-less sessions (unless live), and GC sweeps them to trash
after a startup grace period.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus_skill.core.project_gc import (
    _project_is_empty,
    gc_stale_projects,
    maybe_gc_stale_projects,
)
from argus_skill.core.session import list_sessions


def _mk(
    gr: Path,
    sid: str,
    *,
    name="",
    objective="",
    origin="",
    backlog="",
    pid=None,
    now=100.0,
):
    d = gr / "projects" / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, "created": now, "last_active": now}
    if name:
        meta["display_name"] = name
    if objective:
        meta["objective"] = objective
    if origin:
        meta["origin"] = origin
    session = d / "session.json"
    session.write_text(json.dumps(meta), encoding="utf-8")
    if backlog:
        (d / "backlog.jsonl").write_text(backlog, encoding="utf-8")
    if pid is not None:
        (d / "daemon.pid").write_text(str(pid), encoding="utf-8")
    os.utime(session, (now, now))
    os.utime(d, (now, now))
    return d


# ---- list_sessions include_empty filter ---------------------------------

def test_list_sessions_hides_empty_litter(tmp_path):
    _mk(tmp_path, "s-empty01")                                   # litter
    _mk(tmp_path, "s-named02", name="optimize 079")              # named
    _mk(tmp_path, "s-work03", backlog='{"id":"x","title":"t"}')  # has content
    _mk(tmp_path, "s-live04", pid=os.getpid())                   # live daemon
    _mk(tmp_path, "s-web005", origin="web")                     # deliberate idle Web session

    all_ids = {s.id for s in list_sessions(tmp_path)}
    assert all_ids == {
        "s-empty01", "s-named02", "s-work03", "s-live04", "s-web005",
    }

    kept = {s.id for s in list_sessions(tmp_path, include_empty=False)}
    assert "s-empty01" not in kept           # litter hidden
    assert {"s-named02", "s-work03", "s-live04", "s-web005"} <= kept


# ---- GC empty sweep ------------------------------------------------------

def test_project_is_empty_detection(tmp_path):
    empty = _mk(tmp_path, "s-e1")
    named = _mk(tmp_path, "s-n1", name="x")
    work = _mk(tmp_path, "s-w1", backlog='{"id":"a"}')
    web = _mk(tmp_path, "s-web1", origin="web")
    assert _project_is_empty(empty) is True
    assert _project_is_empty(named) is False
    assert _project_is_empty(work) is False
    assert _project_is_empty(web) is False


def test_gc_protects_recent_empty_session_during_startup(tmp_path):
    # This is the exact Web/TUI race: POST /api/daemons created an idle session,
    # then another user's daemon boot ran GC before the first snapshot/message.
    _mk(tmp_path, "s-empty1", now=200)

    assert gc_stale_projects(tmp_path, now=300.0) == []
    assert (tmp_path / "projects" / "s-empty1").is_dir()


def test_gc_sweeps_empty_after_startup_grace(tmp_path):
    # Empty sessions older than the one-hour grace remain reversible litter and
    # are swept; named/content/live sessions still survive.
    _mk(tmp_path, "s-empty1", now=200)
    _mk(tmp_path, "s-empty2", now=200)
    _mk(tmp_path, "s-named1", name="real", now=200)
    _mk(tmp_path, "s-work1", backlog='{"id":"a"}', now=200)
    _mk(tmp_path, "s-live1", pid=os.getpid(), now=200)
    _mk(tmp_path, "s-web001", origin="web", now=200)

    pruned = set(gc_stale_projects(tmp_path, now=4000.0))
    assert pruned == {"s-empty1", "s-empty2"}
    # survivors still on disk; litter moved to trash (reversible, not deleted)
    for keep in ("s-named1", "s-work1", "s-live1", "s-web001"):
        assert (tmp_path / "projects" / keep).is_dir()
    for gone in ("s-empty1", "s-empty2"):
        assert not (tmp_path / "projects" / gone).exists()
    assert (tmp_path / "projects_trash").exists()


def test_startup_gc_preserves_other_webapi_idle_sessions(tmp_path):
    # Independent developer WebAPIs share ARGUS_SKILL_HOME. Starting one must
    # not invalidate an idle session still selected in another developer's TUI.
    _mk(tmp_path, "s-other1", now=time.time() - 7200)

    assert maybe_gc_stale_projects(tmp_path) == []
    assert (tmp_path / "projects" / "s-other1").is_dir()


def test_gc_sweep_empty_can_be_disabled(tmp_path):
    _mk(tmp_path, "s-empty1", now=200)
    pruned = gc_stale_projects(tmp_path, now=300.0, sweep_empty=False)
    assert pruned == []  # recent empty kept when sweep_empty=False
