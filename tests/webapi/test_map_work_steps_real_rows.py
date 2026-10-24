"""Map work steps built from rows seen in a real session."""

from __future__ import annotations

from argus.core.progress_step import plain_step_label
from argus.webapi.map_view import fold_progress, segment_events

READ = (
    'read: {"cells": null, "includeOutputs": false, "limit": 100, "offset": 1, '
    '"pages": null, "path": "/data/proj/handoffs/0f40/CHECKPOINT.md"}'
)


def _row(status: str, ts: float, **extra) -> dict:
    return {
        "type": "engineer.progress", "kind": "tool_use", "agent_layer": "engineer",
        "text": READ, "status": status, "ts": ts, "tool_name": "read", **extra,
    }


def _steps(rows: list[dict]) -> list[dict]:
    segments: dict = {}
    for row in rows:
        fold_progress(segments, "item-1", row, "explicit")
    (segment,) = segment_events(segments)
    return segment["steps"]


def test_started_then_failed_without_call_id_is_one_failed_step() -> None:
    steps = _steps([_row("running", 1.0), _row("failed", 1.1)])
    assert len(steps) == 1
    assert steps[0]["status"] == "failed"


def test_reports_with_different_call_ids_are_not_merged() -> None:
    steps = _steps([_row("running", 1.0, call_id="a"), _row("failed", 1.1, call_id="b")])
    assert [step["status"] for step in steps] == ["running", "failed"]


def test_saved_tool_label_without_arguments_loses_its_glyph() -> None:
    name = "fetch_copilot_cli_documentation"
    label = plain_step_label(f"⚙ {name}", name, "")
    assert not label.startswith("⚙")
    assert label.strip()


def test_argus_own_tool_is_no_work_step() -> None:
    internal = _row("failed", 1.1, kind="command_execution", tool_name="",
                    text="python -m argus.tools.manager_live_view --state-dir /data/proj status")
    steps = _steps([_row("completed", 1.0), internal])
    assert [step["kind"] for step in steps] == ["tool_use"]
