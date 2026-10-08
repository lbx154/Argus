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
    assert runtime == {"backend": "copilot", "model": "new-model", "effort": "high", "run_label": "reviewer", "role": "reviewer", "ts": 2.0}


def test_latest_call_runtime_is_none_before_any_call(tmp_path: Path) -> None:
    _write(tmp_path / "events.jsonl", [{"type": "life.mission.completed"}])
    assert latest_call_runtime(tmp_path) is None


def test_latest_call_runtime_reports_the_work_call_not_helper_calls(tmp_path: Path) -> None:
    """Classifiers, learning reviews and summaries run on their own (often
    smaller) models; the composer must name the call that did the work."""
    start = {"type": "agent.io.start", "call_id": "x"}
    _write(tmp_path / "events.jsonl", [
        {**start, "backend": "copilot", "model": "fast-model", "reasoning_effort": "low", "run_label": "manager-frontdoor-classify", "ts": 1.0},
        {**start, "backend": "copilot", "model": "work-model", "reasoning_effort": "high", "run_label": "self-implement", "ts": 2.0},
        {**start, "backend": "copilot", "model": "fast-model", "reasoning_effort": "low", "run_label": "manager-classify-grounded", "ts": 3.0},
        {**start, "backend": "copilot", "model": "fast-model", "reasoning_effort": "low", "run_label": "answer-learning", "ts": 4.0},
        {**start, "backend": "copilot", "model": "fast-model", "reasoning_effort": "low", "run_label": "reflection", "ts": 5.0},
        {**start, "backend": "copilot", "model": "fast-model", "reasoning_effort": "low", "run_label": "manager.reviewed_facts", "ts": 6.0},
        {**start, "backend": "copilot", "model": "summary-model", "run_label": "map-summary", "ts": 7.0},
    ])
    runtime = latest_call_runtime(tmp_path)
    assert runtime is not None
    assert (runtime["model"], runtime["effort"], runtime["run_label"]) == ("work-model", "high", "self-implement")
    assert runtime["role"] == "engineer"


def test_latest_call_runtime_names_the_role_of_the_call(tmp_path: Path) -> None:
    _write(tmp_path / "events.jsonl", [
        {"type": "agent.io.start", "backend": "copilot", "model": "m", "run_label": "simple-1", "ts": 1.0},
    ])
    runtime = latest_call_runtime(tmp_path)
    assert runtime is not None and runtime["role"] == "manager"
