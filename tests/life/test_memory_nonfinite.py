from __future__ import annotations

import json
import shutil

import pytest

from argus_skill.life.memory import _read_jsonl_tail_history


@pytest.mark.parametrize("reader", ["tail", "marked", "rg"])
def test_event_history_skips_bad_numbers_across_retained_generations(tmp_path, reader):
    if reader == "rg" and shutil.which("rg") is None:
        pytest.skip("ripgrep fast path requires rg")
    path = tmp_path / "events.jsonl"
    archive = tmp_path / "events.jsonl.1"

    def row(event_id, timestamp):
        return json.dumps({"type": "round.review.completed", "event_id": event_id,
                           "ts": timestamp, "reason": "NaN is ordinary text"}) + "\n"

    archive.write_text(row("bad-first", float("nan")) + row("old", 1), encoding="utf-8")
    path.write_text(row("bad-middle", float("inf")) + row("new", 2)
                    + row("bad-last", 0).replace('"ts": 0', '"ts": 1e400'), encoding="utf-8")
    original = (archive.read_bytes(), path.read_bytes())
    options = {}
    if reader == "marked":
        options["raw_markers"] = (b"round.review.completed",)
    elif reader == "rg":
        options["rg_pattern"] = "round.review.completed"

    events = _read_jsonl_tail_history(path, 3, **options)

    assert [event["event_id"] for event in events] == ["old", "new"]
    assert all(event["reason"] == "NaN is ordinary text" for event in events)
    assert (archive.read_bytes(), path.read_bytes()) == original
