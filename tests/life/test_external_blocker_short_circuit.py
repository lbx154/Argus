"""External-blocker artifact discovery -- generic glob, no dated filenames."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.supervisor import _operator_only_blocker_paths_for_project


def _write_blocker(project_root: Path, filename: str, payload: dict) -> Path:
    diagnosis = project_root / "diagnosis"
    diagnosis.mkdir(parents=True, exist_ok=True)
    path = diagnosis / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_finds_legacy_dated_lock_file(tmp_path: Path):
    """Backwards-compat: the 3c40efa-era dated filename should still match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_lock_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1
    assert paths[0].name == "operator_only_external_blocker_lock_20260605.json"


def test_finds_undated_generic_filename(tmp_path: Path):
    """Forward-compat: new generic filename without date should also match."""
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert len(paths) == 1


def test_returns_empty_when_no_blocker_file(tmp_path: Path):
    (tmp_path / "diagnosis").mkdir()
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_ignores_unrelated_diagnosis_files(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "stage_check_terminal_index.md").write_text("ignore me")
    (diagnosis / "operator_action_required.md").write_text("ignore me")
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    assert paths == []


def test_picks_most_recent_when_multiple(tmp_path: Path):
    import time

    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260601.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["a"],
        },
    )
    time.sleep(0.01)
    p2 = _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["b"],
        },
    )
    paths = _operator_only_blocker_paths_for_project(tmp_path)
    # Most recent first.
    assert paths[0] == p2
