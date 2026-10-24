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
    from argus.life.event_log import iter_call_events

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


def test_iter_call_events_reads_old_and_new_raw_transcripts_alike(
    tmp_path: Path,
) -> None:
    from argus.life.event_log import iter_call_events

    identity = {"call_id": "c", "run_label": "engineer-r1", "backend": "copilot", "model": "m"}
    old_stream = {"type": "agent.io.stream", "io_kind": "stream", **identity,
                  "stream": "stdout", "line": "old", "ts": 1.0,
                  "event_schema_version": 1, "payload_schema_version": 1}
    log_path = tmp_path / "agent_io.jsonl"
    # The start rotated into the previous generation; its frames did not.
    _write(log_path.with_name("agent_io.jsonl.1"), [
        {"type": "agent.io.start", "io_kind": "start", **identity, "prompt": "p"},
        old_stream,
    ])
    _write(log_path, [{"call_id": "c", "stream": "stderr", "line": "new", "ts": 2.0}])

    rows = list(iter_call_events(log_path, "c"))

    assert rows[1] == old_stream
    assert rows[2] == {"type": "agent.io.stream", **identity, "stream": "stderr", "line": "new", "ts": 2.0}
