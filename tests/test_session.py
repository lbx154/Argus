"""Tests for the session model (argus_skill.core.session).

The defining behaviour: ``--new`` mints a FRESH session every time (two runs
from the same cwd are two different sessions), while ``--resume <id>`` /
``--continue`` reuse a previous one. Legacy cwd-fingerprint projects stay
listable/resumable.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.core.session import (
    SessionMeta,
    SessionResolutionError,
    list_sessions,
    most_recent_session,
    new_session_id,
    read_session_meta,
    resolve_session,
    resolve_session_workdir,
    touch_session,
)
from argus_skill.life.memory import MemoryBundle


def test_new_session_id_format():
    sid = new_session_id()
    assert sid.startswith("s-")
    assert "/" not in sid
    assert sid != new_session_id()  # unique


def test_new_mode_mints_fresh_each_time(tmp_path):
    a, new_a = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    b, new_b = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=200)
    assert new_a and new_b
    assert a != b  # SAME cwd, but two different sessions — the whole point
    # Each wrote its session.json
    assert read_session_meta(tmp_path, a).created == 100
    assert read_session_meta(tmp_path, b).created == 200
    assert read_session_meta(tmp_path, a).workdir == str(tmp_path.resolve())


def test_resolve_session_workdir_preserves_legacy_cwd(tmp_path):
    state = tmp_path / "state"
    legacy = tmp_path / "legacy-work"
    launch = tmp_path / "launch-only"
    state.mkdir()
    legacy.mkdir()
    launch.mkdir()

    resolved = resolve_session_workdir(
        SessionMeta(id="legacy", cwd=str(legacy), launch_cwd=str(launch)),
        state_dir=state,
    )

    assert resolved == legacy.resolve()


def test_resolve_session_workdir_rejects_missing_explicit_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_session_workdir(
            SessionMeta(id="missing", workdir=str(tmp_path / "missing")),
            state_dir=tmp_path,
        )


def test_resolve_session_workdir_rejects_missing_legacy_cwd(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_session_workdir(
            SessionMeta(id="legacy", cwd=str(tmp_path / "missing")),
            state_dir=tmp_path,
        )


def test_continue_returns_most_recent(tmp_path):
    a, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    b, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=200)
    assert most_recent_session(tmp_path) == b
    sid, is_new = resolve_session(global_root=tmp_path, mode="continue")
    assert sid == b and not is_new


def test_resume_validates_existence(tmp_path):
    a, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    sid, is_new = resolve_session(global_root=tmp_path, mode="resume", session_id=a)
    assert sid == a and not is_new
    with pytest.raises(SessionResolutionError):
        resolve_session(global_root=tmp_path, mode="resume", session_id="s-doesnotexist")


def test_continue_with_no_sessions_raises(tmp_path):
    with pytest.raises(SessionResolutionError):
        resolve_session(global_root=tmp_path, mode="continue")


def test_list_sessions_newest_first_and_includes_legacy(tmp_path):
    import os
    resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    b, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=300)
    # a legacy cwd-fingerprint project (no session.json), made OLD via utime so
    # the synthetic last_active (= dir mtime) sorts older than session b.
    legacy = tmp_path / "projects" / "07197071cf43"
    legacy.mkdir(parents=True)
    continuous = legacy / "continuous.json"
    continuous.write_text(json.dumps({"objective": "old work"}))
    os.utime(continuous, (50, 50))
    os.utime(legacy, (50, 50))
    sessions = list_sessions(tmp_path)
    ids = [s.id for s in sessions]
    assert b == ids[0]  # newest active first
    assert "07197071cf43" in ids  # legacy still listed (resumable)
    legacy_meta = next(s for s in sessions if s.id == "07197071cf43")
    assert legacy_meta.objective == "old work"


def test_legacy_last_active_ignores_web_projection_writes(tmp_path):
    import os

    legacy = tmp_path / "projects" / "s-legacy"
    legacy.mkdir(parents=True)
    continuous = legacy / "continuous.json"
    continuous.write_text(json.dumps({"objective": "old work"}))
    events = legacy / "events.jsonl"
    events.write_text('{"type":"loop.done"}\n')
    os.utime(continuous, (100, 100))
    os.utime(events, (120, 120))

    # Web snapshot projection and lock creation may touch the directory today,
    # but neither is actual research/session activity.
    (legacy / "mission-view.json").write_text("{}\n")
    (legacy / "mission-view.lock").write_text("")
    (legacy / "usage.jsonl").write_text('{"source":"legacy.events"}\n')
    os.utime(legacy, (1000, 1000))

    meta = next(item for item in list_sessions(tmp_path) if item.id == "s-legacy")
    assert meta.last_active == 120


def test_named_session_last_active_follows_durable_work(tmp_path):
    import os

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    project = tmp_path / "projects" / sid
    events = project / "events.jsonl"
    events.write_text('{"type":"round.start"}\n')
    os.utime(events, (500, 500))
    projection = project / "mission-view.json"
    projection.write_text("{}\n")
    os.utime(projection, (700, 700))

    meta = next(item for item in list_sessions(tmp_path) if item.id == sid)

    assert meta.last_active == 500
    assert read_session_meta(tmp_path, sid).last_active == 100


def test_contentless_legacy_session_does_not_trust_directory_mtime(tmp_path):
    import os

    legacy = tmp_path / "projects" / "s-lock-only"
    legacy.mkdir(parents=True)
    (legacy / "mission-view.lock").write_text("")
    os.utime(legacy, (1000, 1000))

    meta = next(item for item in list_sessions(tmp_path) if item.id == "s-lock-only")
    assert meta.last_active == 0


def test_touch_updates_last_active_and_name(tmp_path):
    a, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=100)
    touch_session(tmp_path, a, display_name="optimize 079 kernel", now=500)
    m = read_session_meta(tmp_path, a)
    assert m.last_active == 500
    assert m.display_name == "optimize 079 kernel"


def test_touch_creates_missing_metadata_with_requested_timestamp(tmp_path):
    (tmp_path / "projects" / "s-missing").mkdir(parents=True)
    touch_session(tmp_path, "s-missing", display_name="Recovered", now=123)
    meta = read_session_meta(tmp_path, "s-missing")
    assert meta is not None
    assert meta.created == 123
    assert meta.last_active == 123
    assert meta.display_name == "Recovered"


def test_memory_bundle_keys_by_session_id_not_cwd(tmp_path):
    # Two bundles with explicit (different) fingerprints -> different roots,
    # even from the same cwd. This is what gives each session its own daemon.
    b1 = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path, fingerprint="s-aaaa1111")
    b2 = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path, fingerprint="s-bbbb2222")
    assert b1.project.root != b2.project.root
    assert b1.project.root.name == "s-aaaa1111"
    assert b2.project.root.name == "s-bbbb2222"


def test_memory_bundle_default_still_cwd(tmp_path):
    # No fingerprint -> legacy cwd identity (unchanged behaviour).
    b = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path)
    assert b.project.root.parent.name == "projects"
    assert b.project.root.name not in ("", "projects")
