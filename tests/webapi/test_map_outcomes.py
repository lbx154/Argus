"""Outcomes survive as history, without becoming another attempt's result."""
import copy
import json

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi.map_feed import MapFeed
from argus.webapi.map_outcomes import project_task_outcome
from argus.webapi.map_view import normalize_events, read_map

PAUSED = {"execution_status": "paused", "review_status": "blocked", "stage_certification": "not_assessed",
          "interruption_kind": "daemon_shutdown", "resumable": True}
COMPLETED = {"execution_status": "completed", "review_status": "done", "stage_certification": "not_certified",
             "interruption_kind": "none", "resumable": False}


def rows():
    return [
        {"id": "start-1", "item_id": "a", "type": "life.mission.started", "ts": 100, "attempt": 1},
        {"id": "review-1", "item_id": "a", "type": "round.review.completed", "ts": 150,
         "round_index": 1, "status": "blocked", "review_skipped": True, "review_source": "reviewer"},
        {"id": "stop-1", "item_id": "a", "type": "life.mission.completed", "ts": 150.2,
         "status": "paused_daemon_shutdown", "outcome": PAUSED},
        {"id": "start-2", "item_id": "a", "type": "life.mission.started", "ts": 200.1, "attempt": 2},
    ]


def task(**patch):
    return {"id": "a", "title": "Verify a restricted claim", "objective": "Check the recorded assumptions",
            "status": "running", "attempt": 2, "started_ts": 200, "finished_ts": None, "outcome": PAUSED, **patch}


def test_map_projection_and_cursor_change_on_a_result_event_without_rewriting_backlog(tmp_path):
    sid = "s-outcome"
    write_session_meta(tmp_path, SessionMeta(id=sid, created=1, last_active=1))
    life = tmp_path / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(**{**task(status="done", finished_ts=239.9, outcome=COMPLETED), "ts": 1}))
    event_path = life / "events.jsonl"
    event_path.write_text(''.join(json.dumps({**event, "event_id": event["id"]}) + '\n' for event in rows()))
    before = (life / "backlog.jsonl").read_bytes()
    original_events = event_path.read_bytes()
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    assert first["tasks"][0]["outcome"] == {}
    assert first["tasks"][0]["recorded_outcome"] == COMPLETED
    assert (life / "backlog.jsonl").read_bytes() == before and event_path.read_bytes() == original_events
    with event_path.open("a") as stream:
        stream.write(json.dumps({"event_id": "done-2", "item_id": "a", "type": "life.mission.completed", "ts": 240,
                                 "attempt": 2, "outcome": COMPLETED}) + '\n')
    next_page = feed.read(sid, tmp_path, life, first["cursor"])
    assert next_page["incremental"] and next_page["tasks"][0]["outcome"] == COMPLETED
    assert next_page["tasks"][0]["revision"] != first["tasks"][0]["revision"]
    assert (life / "backlog.jsonl").read_bytes() == before
    assert len(read_map(sid, tmp_path, life)["events"]) == 5


def test_reviewer_backend_failure_is_not_a_scientific_verdict_or_a_backlog_rewrite():
    failed = {**COMPLETED, "execution_status": "failed", "review_status": "blocked"}
    raw = [
        {"event_id": "start", "item_id": "a", "type": "life.mission.started", "ts": 200, "attempt": 2},
        {"event_id": "review", "item_id": "a", "type": "round.review.completed", "ts": 210,
         "status": "blocked", "review_skipped": True, "backend_unavailable": True},
        {"event_id": "end", "item_id": "a", "type": "life.mission.completed", "ts": 211,
         "status": "error", "outcome": failed},
    ]
    normalized = normalize_events(raw, {"a"})
    original = task(status="failed", finished_ts=211, outcome=failed)
    before = copy.deepcopy((original, normalized))
    result = project_task_outcome(original, normalized)
    assert normalized[1]["backend_unavailable"] is True
    assert result["status"] == "failed"
    assert result["outcome"] == {**failed, "review_status": "unavailable"}
    assert result["recorded_outcome"] == failed
    assert (original, normalized) == before
    # Re-projecting for a task explanation uses the same outcome as the map.
    assert project_task_outcome(result, normalized)["outcome"] == result["outcome"]
