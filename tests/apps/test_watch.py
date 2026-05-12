"""Regression tests for ``argus-skill --watch`` state tracking."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps._watch import _WatchState


def _write_events(path: Path, events: list[dict[str, object]], *, mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "\n".join(json.dumps(event, sort_keys=True) for event in events)
    if blob:
        blob += "\n"
    if mode == "a":
        with path.open("a", encoding="utf-8") as fh:
            fh.write(blob)
    else:
        path.write_text(blob, encoding="utf-8")


def _round_events(
    round_index: int,
    *,
    kind: str = "round.started",
    input_tokens: int = 3,
    output_tokens: int = 2,
    review_input_tokens: int = 5,
    review_output_tokens: int = 7,
) -> list[dict[str, object]]:
    return [
        {"type": kind, "round_index": round_index},
        {
            "type": "round.main.completed",
            "round_index": round_index,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        {
            "type": "round.review.completed",
            "round_index": round_index,
            "input_tokens": review_input_tokens,
            "output_tokens": review_output_tokens,
        },
    ]


def test_watch_state_tracks_memory_backend_round_events(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-abc-123456"},
            *_round_events(1, kind="round.started", input_tokens=800, output_tokens=200,
                           review_input_tokens=100, review_output_tokens=50),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 1
    assert state.mission.tokens_in == 900
    assert state.mission.tokens_out == 250


def test_watch_state_accumulates_more_than_twenty_events(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    events: list[dict[str, object]] = [{"type": "life.mission.started", "item_id": "mission-xyz"}]
    for round_index in range(1, 8):
        events.extend(_round_events(round_index, kind="round.start"))
    events.append({"type": "life.mission.completed", "success": True})
    _write_events(current, events)

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 7
    assert state.mission.tokens_in == 56
    assert state.mission.tokens_out == 63


def test_watch_state_recovers_from_rollover(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    roll = tmp_path / "events.jsonl.1"
    state = _WatchState(events_path=current, roll_path=roll)

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-roll"},
            *_round_events(1, kind="round.start"),
        ],
    )
    state.drain()

    _write_events(current, _round_events(2, kind="round.start"), mode="a")
    current.replace(roll)
    _write_events(
        current,
        [
            *_round_events(3, kind="round.started"),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.rounds == 3
    assert state.mission.tokens_in == 24
    assert state.mission.tokens_out == 27


def test_watch_state_recovers_from_truncation(tmp_path: Path) -> None:
    current = tmp_path / "events.jsonl"
    state = _WatchState(events_path=current, roll_path=tmp_path / "events.jsonl.1")

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-old"},
            *_round_events(1, kind="round.started", input_tokens=10, output_tokens=11,
                           review_input_tokens=12, review_output_tokens=13),
        ],
    )
    state.drain()

    _write_events(
        current,
        [
            {"type": "life.mission.started", "item_id": "mission-new"},
            *_round_events(1, kind="round.started", input_tokens=20, output_tokens=21,
                           review_input_tokens=22, review_output_tokens=23),
            {"type": "life.mission.completed", "success": True},
        ],
    )

    state.drain()

    assert state.mission.status == "done"
    assert state.mission.item_id == "mission-new"
    assert state.mission.rounds == 1
    assert state.mission.tokens_in == 42
    assert state.mission.tokens_out == 44
