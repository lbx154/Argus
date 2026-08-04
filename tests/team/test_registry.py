from __future__ import annotations

from pathlib import Path

from argus_skill.team import registry


def test_write_and_list_marker_roundtrip(tmp_path: Path) -> None:
    p = registry.write_marker(
        tmp_path, team_id="t1", team_root=tmp_path / "teamroot",
        cwd=tmp_path / "ws", now=123.0,
    )
    assert p == tmp_path / ".argus" / "team" / "t1.json"
    (marker,) = registry.list_markers(tmp_path)
    assert marker["team_id"] == "t1"
    assert marker["team_root"] == str(tmp_path / "teamroot")
    assert marker["cwd"] == str(tmp_path / "ws")
    assert marker["created_ts"] == 123.0


def test_list_markers_returns_all_active(tmp_path: Path) -> None:
    registry.write_marker(tmp_path, team_id="t1", team_root=tmp_path / "a", cwd=tmp_path, now=1.0)
    registry.write_marker(tmp_path, team_id="t2", team_root=tmp_path / "b", cwd=tmp_path, now=2.0)
    ids = sorted(m["team_id"] for m in registry.list_markers(tmp_path))
    assert ids == ["t1", "t2"]


def test_list_markers_empty_when_no_dir(tmp_path: Path) -> None:
    assert registry.list_markers(tmp_path) == []


def test_list_markers_skips_corrupt(tmp_path: Path) -> None:
    registry.write_marker(tmp_path, team_id="t1", team_root=tmp_path / "a", cwd=tmp_path, now=1.0)
    (registry.marker_dir(tmp_path) / "bad.json").write_text("{not json", encoding="utf-8")
    ids = [m["team_id"] for m in registry.list_markers(tmp_path)]
    assert ids == ["t1"]


def test_remove_marker(tmp_path: Path) -> None:
    registry.write_marker(tmp_path, team_id="t1", team_root=tmp_path / "a", cwd=tmp_path, now=1.0)
    registry.remove_marker(tmp_path, "t1")
    assert registry.list_markers(tmp_path) == []
    # idempotent: removing a missing marker never raises
    registry.remove_marker(tmp_path, "t1")


def test_team_id_sanitized_in_filename_but_readable(tmp_path: Path) -> None:
    p = registry.write_marker(tmp_path, team_id="sol::opt/43", team_root=tmp_path / "a", cwd=tmp_path, now=1.0)
    assert p.parent == registry.marker_dir(tmp_path)
    assert "/" not in p.name and ":" not in p.name
    # the original id survives in the marker body.
    assert registry.list_markers(tmp_path)[0]["team_id"] == "sol::opt/43"
