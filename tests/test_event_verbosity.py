"""Tests for JsonlEventSink verbosity (signal vs full).

- "full" persists durable play-by-play events, but never transient UI updates.
- "signal" (the daemon default) persists only high-value events, but NEVER
  drops a win/result/error.
- The downstream sink always receives every event regardless of verbosity.
"""

from __future__ import annotations

import json

from argus_skill.life.event_log import JsonlEventSink


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


def test_full_persists_everything(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path, verbosity="full")
    _feed(
        sink,
        {"type": "engineer.progress", "kind": "command_execution", "text": "ls"},
        {"type": "session.roll"},
        {"type": "round.review.completed", "status": "continue"},
    )
    types = [e["type"] for e in _read(tmp_path)]
    assert types == ["engineer.progress", "session.roll", "round.review.completed"]


def test_transient_progress_is_live_only_even_in_full_mode(tmp_path):
    cap = _Capture()
    sink = JsonlEventSink(cap, life_dir=tmp_path, verbosity="full")
    transient = {
        "type": "engineer.progress",
        "kind": "agent_message",
        "text": "growing prefix",
        "replace": True,
        "transient": True,
        "message_id": "m1",
    }
    final = {
        "type": "engineer.progress",
        "kind": "agent_message",
        "text": "final message",
        "replace": True,
        "message_id": "m1",
    }

    _feed(sink, transient, final)

    assert [event["text"] for event in cap.events] == [
        "growing prefix",
        "final message",
    ]
    assert cap.events[0]["transient"] is True
    assert "transient" not in cap.events[1]
    assert [event["text"] for event in _read(tmp_path)] == ["final message"]


def test_default_verbosity_is_signal_clean_episode(tmp_path):
    # No verbosity arg -> clean SIGNAL default (the sellable trajectory): churn
    # dropped, but an error/win is never lost. (Set ARGUS_SKILL_EVENT_VERBOSITY=full to debug.)
    sink = JsonlEventSink(None, life_dir=tmp_path)
    _feed(
        sink,
        {"type": "engineer.progress", "kind": "command_execution", "text": "ls"},  # churn → dropped
        {"type": "round.review.completed", "status": "continue"},  # signal → kept
        {"type": "engineer.progress", "text": "RESULT cand_ms=1.5 correct=true"},  # win → kept
    )
    types = [e["type"] for e in _read(tmp_path)]
    assert "engineer.progress" in types  # the RESULT win survived
    assert "round.review.completed" in types
    assert len(_read(tmp_path)) == 2  # the bare command_execution churn dropped


def test_signal_drops_noise_keeps_signal(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path, verbosity="signal")
    _feed(
        sink,
        {"type": "engineer.progress", "kind": "command_execution", "text": "ls -la"},  # noise
        {"type": "session.roll"},  # noise
        {"type": "round.watchdog.waiting"},  # noise
        {"type": "round.review.completed", "status": "continue"},  # signal
        {"type": "life.manager.intent.completed", "vertical": "learning"},  # signal
        {"type": "life.planner.start", "manager_intent": {"vertical": "learning"}},  # signal
        {"type": "life.planner.task_added", "title": "t"},  # signal
        {"type": "skill.created", "name": "x"},  # signal
        {"type": "loop.done", "text": "status=done"},  # signal
    )
    types = [e["type"] for e in _read(tmp_path)]
    assert types == [
        "round.review.completed",
        "life.manager.intent.completed",
        "life.planner.start",
        "life.planner.task_added",
        "skill.created",
        "loop.done",
    ]


def test_signal_never_drops_a_win_or_error(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path, verbosity="signal")
    _feed(
        sink,
        {
            "type": "engineer.progress",
            "kind": "agent_message",
            "text": "RESULT problem=053 correct=true cand_ms=0.0186",
        },  # WIN in prose
        {
            "type": "engineer.progress",
            "kind": "agent_message",
            "text": "Traceback (most recent call last): RuntimeError",
        },  # ERROR in prose
        {"type": "some.backend_failure", "text": "429"},  # error by type
        {"type": "engineer.progress", "kind": "command_execution", "text": "echo hi"},  # noise
    )
    texts = [e.get("text", "")[:12] for e in _read(tmp_path)]
    assert len(_read(tmp_path)) == 3  # win + 2 errors kept, plain command dropped
    assert any("RESULT" in t for t in texts)


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


def test_env_override_to_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_EVENT_VERBOSITY", "signal")
    sink = JsonlEventSink(None, life_dir=tmp_path)  # no explicit arg -> env
    _feed(sink, {"type": "engineer.progress", "kind": "command_execution", "text": "ls"})
    assert _read(tmp_path) == []  # dropped (env=signal)
