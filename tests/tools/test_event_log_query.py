from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_iter_call_events_filters_top_level_call_id_in_chronological_order(
    tmp_path: Path,
) -> None:
    from argus_skill.life.event_log import iter_call_events

    log_path = tmp_path / "events.jsonl"
    call_id = "engineer-call"
    _write(log_path.with_name("events.jsonl.2"), [
        {"call_id": call_id, "seq": "oldest"},
    ])
    _write(log_path.with_name("events.jsonl.3"), [
        {"call_id": call_id, "seq": "older"},
    ])
    _write(log_path.with_name("events.jsonl.1"), [
        {"call_id": "reviewer-call", "prompt": f"audit {call_id}"},
        {"call_id": call_id, "seq": "recent-roll"},
    ])
    _write(log_path, [
        {"call_id": call_id, "seq": "live"},
    ])

    rows = list(iter_call_events(log_path, call_id))

    assert [row["seq"] for row in rows] == [
        "oldest",
        "older",
        "recent-roll",
        "live",
    ]


def test_iter_call_events_stops_after_generation_containing_call_start(
    tmp_path: Path,
) -> None:
    from argus_skill.life.event_log import iter_call_events

    log_path = tmp_path / "events.jsonl"
    call_id = "current-call"
    log_path.with_name("events.jsonl.2").write_text(
        "{invalid-old-json}\n",
        encoding="utf-8",
    )
    _write(log_path.with_name("events.jsonl.1"), [
        {"type": "agent.io.start", "call_id": call_id, "seq": "start"},
        {"type": "agent.io.stream", "call_id": call_id, "seq": "stream"},
    ])
    _write(log_path, [
        {"type": "agent.io.complete", "call_id": call_id, "seq": "complete"},
        {"type": "usage.recorded", "call_id": call_id, "seq": "usage"},
    ])

    rows = list(iter_call_events(log_path, call_id))

    assert [row["seq"] for row in rows] == [
        "start",
        "stream",
        "complete",
        "usage",
    ]
