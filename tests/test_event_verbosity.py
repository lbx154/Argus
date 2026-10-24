"""Tests for JsonlEventSink verbosity (signal vs full).

- "full" persists durable play-by-play events, but never transient UI updates.
- "signal" (the daemon default) persists only high-value events, but NEVER
  drops a win/result/error.
- The downstream sink always receives every event regardless of verbosity.
"""

from __future__ import annotations

import json

from argus.life.event_log import JsonlEventSink


class _Capture:
    def __init__(self):
        self.events = []

    def handle_event(self, event):
        self.events.append(event)


def _read(life_dir):
    p = life_dir / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _feed(sink, *events):
    for e in events:
        sink.handle_event(e)


def test_downstream_gets_everything_regardless(tmp_path):
    cap = _Capture()
    sink = JsonlEventSink(cap, life_dir=tmp_path, verbosity="signal")
    _feed(
        sink,
        {"type": "engineer.progress", "kind": "command_execution", "text": "ls"},
        {"type": "round.review.completed"},
    )
    # downstream (live observer) sees BOTH; disk only the signal one.
    assert len(cap.events) == 2
    assert len(_read(tmp_path)) == 1
