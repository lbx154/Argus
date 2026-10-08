"""The composer's "running on" label comes from the newest real call."""

from __future__ import annotations

import json
from pathlib import Path

from argus.life.role_activity import latest_call_runtime


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_latest_call_runtime_reads_the_newest_call_start(tmp_path: Path) -> None:
    start = {"type": "agent.io.start", "call_id": "x", "ts": 1.0}
    _write(tmp_path / "events.jsonl", [
        {**start, "backend": "pi", "model": "old-model", "reasoning_effort": "low", "run_label": "engineer"},
        {**start, "backend": "copilot", "model": "new-model", "reasoning_effort": "high", "run_label": "reviewer", "ts": 2.0},
        {**start, "backend": "pi", "model": "summary-model", "run_label": "map-summary", "ts": 3.0},
        *({"type": "agent.io.stream", "call_id": "x"} for _ in range(900)),
    ])
    runtime = latest_call_runtime(tmp_path)
    assert runtime == {"backend": "copilot", "model": "new-model", "effort": "high", "run_label": "reviewer", "ts": 2.0}


def test_latest_call_runtime_is_none_before_any_call(tmp_path: Path) -> None:
    _write(tmp_path / "events.jsonl", [{"type": "life.mission.completed"}])
    assert latest_call_runtime(tmp_path) is None
