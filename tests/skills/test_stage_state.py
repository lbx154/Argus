from __future__ import annotations

import json

import pytest

from argus_skill.verticals.stage_state import StageStateError, validate_stage_status


def test_stage_status_requires_exact_structured_state(tmp_path):
    path = tmp_path / "research" / "PIPELINE_STATE.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"stages": {"submission": {"status": "blocked"}}}),
        encoding="utf-8",
    )
    with pytest.raises(StageStateError):
        validate_stage_status(
            tmp_path,
            stage="submission",
            allowed_statuses={"ready", "done"},
        )

    path.write_text(
        json.dumps({"stages": {"submission": {"status": "ready"}}}),
        encoding="utf-8",
    )
    assert validate_stage_status(
        tmp_path,
        stage="submission",
        allowed_statuses={"ready", "done"},
    ) == path


def test_stage_status_rejects_symlink_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-pipeline-state.json"
    outside.write_text(
        json.dumps({"stages": {"submission": {"status": "ready"}}}),
        encoding="utf-8",
    )
    path = tmp_path / "research" / "PIPELINE_STATE.json"
    path.parent.mkdir()
    path.symlink_to(outside)
    with pytest.raises(StageStateError, match="outside"):
        validate_stage_status(
            tmp_path,
            stage="submission",
            allowed_statuses={"ready", "done"},
        )
