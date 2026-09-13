"""Invalid numeric event data must not poison snapshots or the event transport."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.json_codec import loads_finite_json
from argus_skill.core.mission_view import load_mission_view, snapshot_mission_view
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.webapi.server import create_app, tail_events


def _review(event_id, ts):
    return {"type": "round.review.completed", "item_id": "A", "status": "continue",
            "reason": "Add evidence", "round_index": 1, "event_id": event_id, "ts": ts}


def _append_bad_then_good(path, token):
    bad = json.dumps(_review("bad", 0)).replace('"ts": 0', '"ts": ' + token)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(bad + "\n" + json.dumps(_review("good", 3)) + "\n")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nonfinite_event_does_not_break_http_views(tmp_path, token):
    sid = "s-nonfinite"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    Backlog(life / "backlog.jsonl").add(BacklogItem.new(item_id="A", title="A", objective="Work"))
    sink = JsonlEventSink(None, life_dir=life)
    sink.append({"type": "life.mission.started", "item_id": "A", "title": "A", "objective": "Work", "ts": 1})
    _append_bad_then_good(life / "events.jsonl", token)
    original = (life / "events.jsonl").read_bytes()
    client = TestClient(create_app(global_root=tmp_path))
    for endpoint in ("snapshot", "events", "events?view=ui", "map"):
        response = client.get(f"/api/projects/{sid}/{endpoint}")
        assert response.status_code == 200, response.text
        loads_finite_json(response.content)
        if endpoint.startswith("events"):
            event_ids = [event.get("event_id") for event in response.json()["events"]]
            assert "bad" not in event_ids
            assert "good" in event_ids
        elif endpoint == "map":
            event_ids = [event["id"] for event in response.json()["events"]]
            assert "bad" not in event_ids
            assert "good" in event_ids
    view = snapshot_mission_view(life, session={}, daemon={}, roles=[], backlog=[])
    assert view["review"]["rejected_attempts"] == 1
    assert view["projection_sync"]["skipped_rows"] == 1
    assert view["projection_sync"]["status"] == "current"
    assert (life / "events.jsonl").read_bytes() == original


def test_checkpoint_poisoned_by_older_reader_is_rebuilt(tmp_path):
    sink = JsonlEventSink(None, life_dir=tmp_path)
    sink.append({"type": "life.mission.started", "item_id": "A", "title": "A", "objective": "Work", "ts": 1})
    _append_bad_then_good(tmp_path / "events.jsonl", "NaN")
    view = load_mission_view(tmp_path)
    view["timeline"][0]["ts"] = float("nan")
    checkpoint = tmp_path / "mission-view.json"
    checkpoint.write_text(json.dumps(view), encoding="utf-8")
    repaired = snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[])
    json.dumps(repaired, allow_nan=False)
    assert repaired["review"]["rejected_attempts"] == 1
    assert repaired["projection_sync"]["skipped_rows"] == 1
    loads_finite_json(checkpoint.read_bytes())


def test_live_event_tail_skips_nonfinite_record_and_continues(tmp_path):
    path = tmp_path / "events.jsonl"
    path.touch()

    async def read_one():
        stream = tail_events(tmp_path, replay_limit=0, poll_interval=0.01)
        waiter = asyncio.create_task(anext(stream))
        try:
            await asyncio.sleep(0.04)
            _append_bad_then_good(path, "Infinity")
            return await asyncio.wait_for(waiter, timeout=1)
        finally:
            await stream.aclose()

    assert asyncio.run(read_one())["event_id"] == "good"
