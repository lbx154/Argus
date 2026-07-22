"""Tests for the B-line full-trajectory bundle (存全量, no stripping)."""
from __future__ import annotations

import json

from argus_skill.tools.trajectory_bundle import (
    _BUNDLE_SCHEMA_VERSION,
    bundle_project,
    find_codex_sessions_by_thread_ids,
)


def _make_project(tmp_path):
    proj = tmp_path / "proj-alpha"
    proj.mkdir()
    (proj / "events.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    (proj / "decisions.jsonl").write_text('{"d":1}\n', encoding="utf-8")
    # backlog/inbox intentionally absent -> `missing`
    return proj


def test_bundle_copies_layers_and_writes_manifest(tmp_path):
    proj = _make_project(tmp_path)
    out = tmp_path / "bundle"
    m = bundle_project(proj, out, now=1000.0)

    # manifest written + copied files exist, FULL content preserved (no strip).
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == _BUNDLE_SCHEMA_VERSION
    assert manifest["project_label"] == "proj-alpha"
    assert manifest["created_ts"] == 1000.0
    assert (out / "events" / "events.jsonl").read_text() == '{"a":1}\n{"a":2}\n'

    layers = {ll["layer"]: ll for ll in manifest["layers"]}
    assert layers["events"]["lines"] == 2
    assert layers["events"]["rel"] == "events/events.jsonl"
    assert m.total_bytes == sum(ll["bytes"] for ll in manifest["layers"])


def test_bundle_records_missing_layers(tmp_path):
    proj = _make_project(tmp_path)
    m = bundle_project(proj, tmp_path / "b", now=1.0)
    present = {ll.layer for ll in m.layers}
    assert present == {"events", "decisions"}
    # absent layers are reported, not fabricated
    for absent in ("backlog", "inbox"):
        assert absent in m.missing


def test_bundle_includes_caller_supplied_codex_sessions(tmp_path):
    proj = _make_project(tmp_path)
    sess = tmp_path / "rollout-xyz.jsonl"
    sess.write_text('{"payload":"tool_call"}\n', encoding="utf-8")
    out = tmp_path / "b2"
    m = bundle_project(proj, out, codex_session_paths=[sess], now=2.0)
    assert (out / "codex" / "rollout-xyz.jsonl").exists()
    assert any(ll.layer == "codex" for ll in m.layers)


def test_bundle_dry_run_writes_nothing(tmp_path):
    proj = _make_project(tmp_path)
    out = tmp_path / "dry"
    m = bundle_project(proj, out, copy=False, now=3.0)
    assert not out.exists()  # nothing written
    assert {ll.layer for ll in m.layers} == {"events", "decisions"}


def _make_codex_root(tmp_path):
    """A synthetic ~/.codex/sessions tree with rollout-<ts>-<thread_id>.jsonl."""
    root = tmp_path / "codex" / "sessions" / "2026" / "07" / "01"
    root.mkdir(parents=True)
    tid_a = "019f0db7-2b70-7760-a733-e0f7df2f9ab8"
    tid_b = "019f0dc9-aaaa-7760-b733-e0f7df2f0000"
    (root / f"rollout-2026-07-01T10-00-00-{tid_a}.jsonl").write_text(
        '{"payload":"a"}\n', encoding="utf-8")
    (root / f"rollout-2026-07-01T11-00-00-{tid_b}.jsonl").write_text(
        '{"payload":"b"}\n', encoding="utf-8")
    (root / "rollout-2026-07-01T12-00-00-unrelated-uuid.jsonl").write_text(
        '{"payload":"c"}\n', encoding="utf-8")
    return tmp_path / "codex" / "sessions", tid_a, tid_b


def test_find_codex_sessions_by_thread_ids_matches_by_filename(tmp_path):
    codex_root, tid_a, tid_b = _make_codex_root(tmp_path)
    hits = find_codex_sessions_by_thread_ids([tid_a], codex_root=codex_root)
    assert len(hits) == 1 and tid_a in hits[0]
    both = find_codex_sessions_by_thread_ids([tid_a, tid_b], codex_root=codex_root)
    assert len(both) == 2
    # unknown id → no match; fail-soft, not an error
    assert find_codex_sessions_by_thread_ids(["nope"], codex_root=codex_root) == []
    assert find_codex_sessions_by_thread_ids([], codex_root=codex_root) == []
    # missing root → []
    assert find_codex_sessions_by_thread_ids([tid_a], codex_root=tmp_path / "gone") == []


def test_bundle_auto_resolves_thread_ids(tmp_path):
    proj = _make_project(tmp_path)
    codex_root, tid_a, _ = _make_codex_root(tmp_path)
    out = tmp_path / "b3"
    m = bundle_project(proj, out, thread_ids=[tid_a], codex_root=codex_root, now=4.0)
    codex_layers = [ll for ll in m.layers if ll.layer == "codex"]
    assert len(codex_layers) == 1
    assert tid_a in codex_layers[0].rel
    assert (out / codex_layers[0].rel).exists()


def test_bundle_dedups_explicit_and_thread_id_codex(tmp_path):
    proj = _make_project(tmp_path)
    codex_root, tid_a, _ = _make_codex_root(tmp_path)
    # the same rollout supplied both explicitly AND via its thread id → counted once
    explicit = next(codex_root.rglob(f"*{tid_a}*.jsonl"))
    out = tmp_path / "b4"
    m = bundle_project(
        proj, out, codex_session_paths=[explicit], thread_ids=[tid_a],
        codex_root=codex_root, now=5.0)
    codex_layers = [ll for ll in m.layers if ll.layer == "codex"]
    assert len(codex_layers) == 1  # de-duplicated, not bundled twice
