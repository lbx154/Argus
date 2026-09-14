from __future__ import annotations

import json
from pathlib import Path

from argus.core.pipeline_state import (
    legacy_pipeline_state_path,
    pipeline_state_path,
    primary_pipeline_state_path,
    read_pipeline_state,
    write_pipeline_state,
)


def test_legacy_state_is_read_without_becoming_authoritative(tmp_path: Path) -> None:
    legacy = legacy_pipeline_state_path(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"vertical": "research"}), encoding="utf-8")

    assert pipeline_state_path(tmp_path) == legacy
    assert read_pipeline_state(tmp_path)["vertical"] == "research"


def test_first_write_migrates_to_generic_path(tmp_path: Path) -> None:
    legacy = legacy_pipeline_state_path(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"vertical": "math"}), encoding="utf-8")

    payload = read_pipeline_state(tmp_path)
    payload["current_stage"] = "solve"
    path = write_pipeline_state(tmp_path, payload)

    assert path == primary_pipeline_state_path(tmp_path)
    assert pipeline_state_path(tmp_path) == path
    assert read_pipeline_state(tmp_path)["current_stage"] == "solve"
    assert json.loads(legacy.read_text(encoding="utf-8")) == {"vertical": "math"}


def test_primary_state_wins_over_stale_legacy_state(tmp_path: Path) -> None:
    legacy = legacy_pipeline_state_path(tmp_path)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"vertical": "research"}), encoding="utf-8")
    write_pipeline_state(tmp_path, {"vertical": "software"})

    assert read_pipeline_state(tmp_path)["vertical"] == "software"
