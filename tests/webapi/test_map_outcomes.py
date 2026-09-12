"""Outcomes survive as history, without becoming another attempt's result."""
import copy
import json

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi.map_feed import MapFeed
from argus_skill.webapi.map_narrative import card_evidence
from argus_skill.webapi.map_outcomes import project_task_outcome
from argus_skill.webapi.map_view import normalize_events, read_map

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


def test_real_failure_pattern_retains_pause_but_does_not_narrate_it_as_attempt_two():
    # Real v11 failure: current running attempt 2 has only its start event while
    # the backlog still contains attempt 1's daemon_shutdown outcome.
    original, events = task(), rows()
    before = copy.deepcopy((original, events))
    projected = project_task_outcome(original, events)
    assert projected["status"] == "running" and projected["outcome"] == {}
    assert projected["recorded_outcome"] == PAUSED
    assert projected["recorded_outcome_source"] == {
        "status": "historical_attempt", "event_id": "stop-1", "event_type": "life.mission.completed",
        "event_ts": 150.2, "event_attempt": None, "attempt": 1, "start_event_id": "start-1", "start_event_ts": 100,
    }
    assert projected["outcome_source"] == {
        "status": "not_recorded_for_current_attempt", "attempt": 2, "started_ts": 200,
        "start_event_id": "start-2", "start_event_ts": 200.1, "reason": "task_unsettled",
    }
    dataset = {"tasks": [original], "events": [{**event, "text": ""} for event in events]}
    evidence = card_evidence(dataset, [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["start-2"]}])[0]
    assert evidence["task"]["outcome"] == {}
    assert evidence["task"]["attempt"] == 2 and evidence["task"]["started_ts"] == 200
    assert "recorded_outcome" not in evidence["task"]
    assert "daemon_shutdown" not in json.dumps(evidence)
    assert (original, events) == before


def test_old_outcome_without_retained_start_is_unbound_instead_of_inventing_a_source():
    projected = project_task_outcome(task(), [rows()[-1]])
    assert projected["outcome"] == {} and projected["recorded_outcome"] == PAUSED
    assert projected["recorded_outcome_source"] == {"status": "unbound"}
    # No current attempt boundary: even a matching terminal value is not proof
    # that a legacy backlog record belongs to the current task attempt.
    unknown = project_task_outcome(task(status="done", started_ts=None, attempt=None), [rows()[2]])
    assert unknown["outcome"] == {}
    assert unknown["outcome_source"]["reason"] == "start_boundary_unavailable"


def test_paused_to_pending_to_running_keeps_history_without_reusing_a_settlement():
    paused = project_task_outcome(task(status="paused_daemon_shutdown", attempt=1, started_ts=99.9, finished_ts=150), rows()[:3])
    assert paused["outcome"] == PAUSED and paused["outcome_source"]["attempt"] == 1
    pending = project_task_outcome(task(status="pending", started_ts=None), rows()[:3])
    running = project_task_outcome(task(), rows())
    for result in (pending, running):
        assert result["outcome"] == {}
        assert result["recorded_outcome"] == PAUSED
        assert result["recorded_outcome_source"]["status"] == "historical_attempt"


def test_current_completed_result_is_available_with_exact_event_and_attempt_source():
    done = {"id": "done-2", "item_id": "a", "type": "life.mission.completed", "ts": 240,
            "attempt": 2, "status": "done", "outcome": COMPLETED}
    current = project_task_outcome(task(status="done", finished_ts=239.9, outcome=COMPLETED), [*rows(), done])
    assert current["outcome"] == COMPLETED
    assert current["outcome_source"]["event_id"] == "done-2"
    assert current["outcome_source"]["attempt"] == 2
    assert current["outcome_source"]["start_event_id"] == "start-2"
    assert current["recorded_outcome_source"]["status"] == "current_attempt"
    # The result belongs to the event, not merely an equal-looking backlog field.
    mismatched = project_task_outcome(task(status="done", finished_ts=239.9), [*rows(), done])
    assert mismatched["outcome"] == COMPLETED and mismatched["recorded_outcome"] == PAUSED


def test_timestamp_window_cannot_override_an_explicit_different_attempt_or_task():
    late_old = {"id": "late-old", "item_id": "a", "type": "life.mission.completed", "ts": 250, "attempt": 1, "outcome": PAUSED}
    foreign = {**late_old, "id": "other-task", "item_id": "b", "attempt": 2, "outcome": COMPLETED}
    projected = project_task_outcome(task(status="done", outcome=COMPLETED), [*rows(), late_old, foreign])
    assert projected["outcome"] == {}
    assert projected["outcome_source"]["reason"] == "no_matching_terminal_event"
    later = {"id": "start-3", "item_id": "a", "type": "life.mission.started", "ts": 230, "attempt": 3}
    unnumbered_end = {"id": "end-3", "item_id": "a", "type": "life.mission.completed", "ts": 260, "outcome": COMPLETED}
    assert project_task_outcome(task(status="done"), [*rows(), later, unnumbered_end])["outcome"] == {}


def test_legacy_terminal_event_can_bind_to_an_explicit_current_claim_without_assuming_review():
    legacy = {"id": "legacy-end", "item_id": "a", "type": "life.mission.completed", "ts": 210,
              "status": "done", "success": True}
    result = project_task_outcome(task(status="done", finished_ts=210, outcome={}), [legacy])
    assert result["outcome"] == {"execution_status": "completed"}
    assert result["outcome_source"]["started_ts"] == 200
    assert result["outcome_source"]["event_id"] == "legacy-end"
    assert "review_status" not in result["outcome"]


def test_historical_card_does_not_borrow_a_later_success_or_current_attempt_metadata():
    events = [{**event, "text": ""} for event in rows()]
    events.append({"id": "done-2", "item_id": "a", "type": "life.mission.completed", "ts": 240,
                   "attempt": 2, "status": "done", "text": "Later success", "outcome": COMPLETED})
    current = task(status="done", finished_ts=240, outcome=COMPLETED)
    document = card_evidence({"tasks": [current], "events": events}, [
        {"key": "stop-1", "task_id": "a", "kind": "result", "event_ids": ["review-1", "stop-1"]},
    ])[0]
    assert "outcome" not in document["task"] and "outcome_source" not in document["task"]
    assert "attempt" not in document["task"] and "started_ts" not in document["task"]
    assert document["events"][1]["outcome"] == PAUSED
    assert "Later success" not in json.dumps(document)


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


def test_normalized_raw_events_keep_the_old_outcome_after_current_projection():
    normalized = normalize_events([{**event, "event_id": event["id"]} for event in rows()], {"a"})
    before = copy.deepcopy(normalized)
    result = project_task_outcome(task(), normalized)
    assert result["outcome"] == {} and result["recorded_outcome"] == PAUSED
    assert normalized == before and normalized[2]["outcome"] == PAUSED
