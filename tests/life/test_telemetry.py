from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.telemetry import (
    TELEMETRY_FILE,
    MissionTelemetryMonitor,
    read_latest_telemetry,
)


def _jsonl_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_mission_telemetry_tracks_incremental_jsonl_progress(tmp_path: Path) -> None:
    life_dir = tmp_path / "life"
    workdir = tmp_path / "repo"
    result_file = workdir / "results" / "run.jsonl"
    result_file.parent.mkdir(parents=True)
    result_file.write_text('{"id": 1}\n', encoding="utf-8")

    monitor = MissionTelemetryMonitor(
        life_dir=life_dir,
        workdir=workdir,
        item_id="task-1",
        title="Run benchmark",
        interval_seconds=1.0,
        scan_dirs=("results",),
    )

    first = monitor.tick_once()
    result_file.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    second = monitor.tick_once()

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert second["files"][0]["path"] == "results/run.jsonl"
    assert second["files"][0]["new_lines"] == 1
    assert second["files"][0]["line_count"] == 2
    latest = read_latest_telemetry(life_dir)
    assert latest is not None
    assert latest["seq"] == 2
    assert len(_jsonl_events(life_dir / TELEMETRY_FILE)) == 2


def test_mission_telemetry_stop_publishes_idle_snapshot(tmp_path: Path) -> None:
    life_dir = tmp_path / "life"
    workdir = tmp_path / "repo"
    (workdir / "results").mkdir(parents=True)
    monitor = MissionTelemetryMonitor(
        life_dir=life_dir,
        workdir=workdir,
        item_id="task-2",
        title="Long experiment",
        interval_seconds=1.0,
        scan_dirs=("results",),
    )

    monitor.start()
    final = monitor.stop()

    latest = read_latest_telemetry(life_dir)
    assert final["running"] is False
    assert latest is not None
    assert latest["running"] is False
    assert latest["item_id"] == "task-2"
    assert "ended_ts" in latest
