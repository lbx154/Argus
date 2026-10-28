"""Tests for the session model (argus.core.session).

The defining behaviour: ``--new`` mints a FRESH session every time (two runs
from the same cwd are two different sessions), while ``--resume <id>`` /
``--continue`` reuse a previous one. Legacy cwd-fingerprint projects stay
listable/resumable.
"""
from __future__ import annotations

import json

import pytest

from argus.core.session import (
    SessionResolutionError,
    list_sessions,
    new_session_id,
    resolve_session,
)
from argus.life.memory import MemoryBundle


def test_new_session_id_format():
    sid = new_session_id()
    assert sid.startswith("s-")
    assert "/" not in sid
    assert sid != new_session_id()  # unique


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


def test_memory_bundle_keys_by_session_id_not_cwd(tmp_path):
    # Two bundles with explicit (different) fingerprints -> different roots,
    # even from the same cwd. This is what gives each session its own daemon.
    b1 = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path, fingerprint="s-aaaa1111")
    b2 = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path, fingerprint="s-bbbb2222")
    assert b1.project.root != b2.project.root
    assert b1.project.root.name == "s-aaaa1111"
    assert b2.project.root.name == "s-bbbb2222"
