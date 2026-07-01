"""Tests for the B-line full-trajectory bundle (存全量, no stripping)."""
from __future__ import annotations

import json

from argus_skill.tools.trajectory_bundle import (
    _BUNDLE_SCHEMA_VERSION,
    bundle_project,
)


def _make_project(tmp_path):
    proj = tmp_path / "proj-alpha"
    proj.mkdir()
    (proj / "events.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    (proj / "decisions.jsonl").write_text('{"d":1}\n', encoding="utf-8")
    (proj / "activity.log").write_text("line1\nline2\nline3\n", encoding="utf-8")
    # memory/journal/backlog/telemetry/inbox intentionally absent → `missing`
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
    assert (out / "activity" / "activity.log").read_text() == "line1\nline2\nline3\n"

    layers = {ll["layer"]: ll for ll in manifest["layers"]}
    assert layers["events"]["lines"] == 2
    assert layers["activity"]["lines"] == 3
    assert layers["events"]["rel"] == "events/events.jsonl"
    assert m.total_bytes == sum(ll["bytes"] for ll in manifest["layers"])


def test_bundle_records_missing_layers(tmp_path):
    proj = _make_project(tmp_path)
    m = bundle_project(proj, tmp_path / "b", now=1.0)
    present = {ll.layer for ll in m.layers}
    assert present == {"events", "decisions", "activity"}
    # absent layers are reported, not fabricated
    for absent in ("memory", "journal", "backlog", "telemetry", "inbox"):
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
    assert {ll.layer for ll in m.layers} == {"events", "decisions", "activity"}
