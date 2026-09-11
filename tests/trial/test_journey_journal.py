"""Synthetic fixtures only: no live tenant data, services or provider calls."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.trial import journey_journal as journal_module
from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.interaction_capture import Capture
from argus_skill.trial.journey_journal import Journal


@pytest.fixture
def setup(tmp_path):
    now = [2_000_000_000.0]
    analytics = Analytics(
        tmp_path / "operator",
        {tenant: {"data_dir": str(tmp_path / tenant), "internal_test": False}
         for tenant in ("tenant-one", "tenant-two")},
        tmp_path / "unused-trial.sqlite3", tmp_path / "unused-compute.sqlite3",
        clock=lambda: now[0],
    )
    analytics.record_consent("tenant-one", analytics.notice_version)
    paths = {}
    for tenant in analytics.tenants:
        path = analytics.tenants[tenant]["data_dir"] / "home/.argus-skill/projects/s-project"
        path.mkdir(parents=True)
        (path / "session.json").write_text(json.dumps({
            "id": "s-project", "display_name": "Synthetic test project",
        }))
        (path / "events.jsonl").touch()
        paths[tenant] = path
    return analytics, Journal(analytics), now, paths


def append(path, *rows):
    with (path / "events.jsonl").open("a") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def replay(journal, **kwargs):
    return journal.replay("tenant-one", "s-project", **kwargs)


def runtime(result):
    return [event for event in result["events"] if event["source_kind"] == "runtime_event"]


def test_repeat_poll_restart_and_ingest_not_source_time(setup):
    analytics, journal, now, paths = setup
    append(paths["tenant-one"],
           {"type": "ui.operator", "text": "Make a plot", "ts": now[0]},
           {"type": "life.mission.started", "item_id": "task-real", "ts": now[0] - 50})
    assert journal.poll()["inserted_events"] == 2
    first = replay(journal)
    assert journal.poll()["inserted_events"] == 0
    journal = Journal(analytics)
    assert journal.poll()["inserted_events"] == 0
    assert replay(journal)["events"] == first["events"]
    assert len({event["id"] for event in first["events"]}) == 2
    assert first["events"][1]["sequence"] > first["events"][0]["sequence"]
    assert first["events"][1]["source_timestamp"] < first["events"][0]["source_timestamp"]
    assert first["events"][0]["task_id"] is None
    assert first["events"][0]["association"]["kind"] == "unassigned"
    assert first["events"][1]["task_id"] == "task-real"
    assert first["events"][1]["association"] == {"kind": "authoritative", "evidence": ["item_id"]}


def test_full_lifecycle_continues_after_browser_close(setup):
    _, journal, now, paths = setup
    path = paths["tenant-one"]
    append(path, {"type": "ui.operator", "text": "Create the artifact"},
           {"type": "life.mission.started", "item_id": "actual-task", "ts": now[0]})
    journal.poll()
    before = replay(journal)["next_sequence"]
    now[0] += 60
    append(path,
           {"type": "life.operator_question.pending", "item_id": "actual-task",
            "question": "Which units?"},
           {"type": "life.mission.completed", "item_id": "actual-task",
            "status": "paused_operator", "success": False},
           {"type": "ui.operator", "text": "Correction: use seconds"},
           {"type": "life.operator_question.answered", "item_id": "actual-task",
            "answer": "Seconds", "continuation_item_id": "next-task",
            "decision_id": "decision-actual-task", "decision_revision": 1,
            "manager_decision": "Use seconds"},
           {"type": "life.mission.resumed", "item_id": "actual-task"},
           {"type": "engineer.progress", "kind": "agent_message", "text": "Generating plot"},
           {"type": "life.mission.failed", "item_id": "actual-task",
            "error_code": "tool_timeout"},
           {"type": "life.mission.cancelled", "item_id": "cancel-task"},
           {"type": "life.mission.started", "item_id": "next-task"},
           {"type": "life.mission.completed", "item_id": "next-task",
            "status": "done", "success": True,
            "delivery": {"targets": [{"path": "outputs/plot.svg", "size_bytes": 1234,
                                     "contents": "NEVER_COPY_FILE"}]}},
           {"type": "ui.argus", "item_id": "next-task", "text": "The plot is ready"})
    journal.poll()
    result = replay(journal)
    assert [item["state"] for item in result["lifecycle"]] == [
        "start", "pause", "resume", "fail", "cancel", "start", "complete",
    ]
    newer = replay(journal, after_sequence=before)
    assert len(newer["events"]) == 11
    assert any(event["payload"].get("answer") == "Seconds" for event in newer["events"])
    resolution = next(event for event in newer["events"] if event["kind"] == "life.operator_question.answered")
    assert resolution["task_id"] == "actual-task"
    assert resolution["payload"]["continuation_item_id"] == "next-task"
    assert resolution["payload"]["decision_id"] == "decision-actual-task"
    assert resolution["payload"]["decision_revision"] == 1
    assert resolution["payload"]["manager_decision"] == "Use seconds"
    assert "resolution_id" not in resolution["payload"]  # Not emitted by this source; do not synthesize it.
    assert newer["artifact_references"][0]["task_id"] == "next-task"
    assert newer["artifact_references"][0]["reference"]["path"] == "outputs/plot.svg"
    assert "NEVER_COPY_FILE" not in json.dumps(result)
    assert result["completeness"]["complete"] is False
    append(path, {"type": "round.review.completed", "item_id": "next-task", "status": "done"})
    journal.poll()
    reviewed = replay(journal, after_sequence=result["next_sequence"])["events"]
    assert len(reviewed) == 1 and reviewed[0]["kind"] == "round.review.completed"
    assert reviewed[0]["payload"]["status"] == "done" and reviewed[0]["task_id"] == "next-task"


def test_public_greeting_stages_and_unconfirmed_tools_preserve_provenance_without_reasoning(setup):
    _, journal, now, paths = setup
    activity = {"kind": "activity", "label": "Prepare the visible greeting", "status": "completed",
                "started_ts": now[0], "ended_ts": now[0] + 1}
    tool = {"kind": "tool_use", "label": "Check the declared artifact", "status": "unconfirmed",
            "tool": "read", "call_id": "actual-call", "started_ts": now[0],
            "ended_ts": now[0] + 2, "output": "RAW_TOOL_OUTPUT_MUST_NOT_COPY",
            "detail": "RAW_COMMAND_MUST_NOT_COPY"}
    append(paths["tenant-one"],
           {"type": "manager.activity", "turn_id": "web-turn-one", "role": "planner",
            "label": "Prepare the greeting", "ts": now[0]},
           {"type": "ui.argus", "text": "Hello", "steps": [activity], "steps_incomplete": False},
           {"type": "ui.argus", "result": {"reply": "Check pending", "steps": [tool],
                                          "steps_incomplete": True}},
           {"type": "ui.argus", "text": "Unsafe", "steps": [
               {"kind": "analysis", "text": "HIDDEN_REASONING_MUST_NOT_COPY"}]},
           {"type": "ui.argus", "text": "Many observed stages", "steps": [activity] * 81})
    journal.poll()
    result = replay(journal)
    rows = runtime(result)
    assert rows[0]["kind"] == "manager.activity"
    assert rows[0]["payload"]["turn_id"] == "web-turn-one"
    assert rows[0]["payload"]["role"] == "planner"
    assert rows[0]["task_id"] is None
    assert rows[0]["association"] == {
        "kind": "unassigned", "evidence": ["no_explicit_task_id"],
        "turn_id": "web-turn-one", "turn_evidence": ["turn_id"],
    }
    assert rows[1]["payload"]["steps"] == [activity]
    assert rows[1]["payload"]["steps_incomplete"] is False
    assert rows[2]["payload"]["result"]["steps"][0]["status"] == "unconfirmed"
    assert {"public_steps_incomplete", "public_step_completion_unconfirmed",
            "public_step_details_excluded"} <= set(rows[2]["warnings"])
    assert "public_steps_truncated" in rows[3]["warnings"]
    serialized = json.dumps(result)
    assert "RAW_TOOL_OUTPUT_MUST_NOT_COPY" not in serialized
    assert "RAW_COMMAND_MUST_NOT_COPY" not in serialized
    assert "HIDDEN_REASONING_MUST_NOT_COPY" not in serialized
    assert result["completeness"]["complete"] is False and result["completeness"]["gap_events"] >= 3


@pytest.mark.parametrize("mode", ["truncate", "rotate", "rewrite"])
def test_discontinuities_are_persistent_gap_observations(setup, mode):
    analytics, journal, _, paths = setup
    path = paths["tenant-one"]
    append(path, {"type": "ui.operator", "text": "original-" * 20})
    journal.poll()
    source = path / "events.jsonl"
    if mode == "rotate":
        source.rename(path / "events.jsonl.1")
        append(path, {"type": "ui.argus", "text": "new"})
    elif mode == "rewrite":
        size = source.stat().st_size
        text = json.dumps({"type": "ui.argus", "text": "replaced"})
        source.write_text(text + " " * (size - len(text) - 1) + "\n")
    else:
        source.write_text("")
        journal.poll()
        append(path, {"type": "ui.argus", "text": "new"})
    journal = Journal(analytics)
    journal.poll()
    result = replay(journal)
    assert len(runtime(result)) == 2
    gaps = [event for event in result["events"] if event["source_kind"] == "journal_gap"]
    assert len(gaps) == 1
    assert gaps[0]["payload"]["code"] == (
        "source_rotated" if mode == "rotate" else "source_truncated_or_rewritten"
    )
    assert result["completeness"]["state"] == "incomplete"
    assert journal.poll()["inserted_events"] == 0


def test_partial_line_not_invented_then_resumes(setup):
    analytics, journal, _, paths = setup
    source = paths["tenant-one"] / "events.jsonl"
    line = json.dumps({"type": "life.mission.completed", "item_id": "x", "status": "done"})
    source.write_text(line)
    assert journal.poll()["inserted_events"] == 0
    result = replay(journal)
    assert result["sources"]["runtime"]["state"] == "awaiting_newline"
    assert result["lifecycle"] == []
    assert result["completeness"]["state"] == "incomplete"
    with source.open("a") as stream:
        stream.write("\n")
    journal = Journal(analytics)
    assert journal.poll()["inserted_events"] == 1
    assert replay(journal)["lifecycle"][0]["state"] == "complete"


def test_oversized_line_skip_is_bounded_and_recovers(setup):
    _, journal, _, paths = setup
    source = paths["tenant-one"] / "events.jsonl"
    source.write_text("x" * (journal_module.MAX_READ_BYTES * 2 + 50))
    assert journal.poll()["inserted_events"] == 1
    assert replay(journal)["sources"]["runtime"]["offset"] == journal_module.MAX_READ_BYTES
    assert replay(journal)["sources"]["runtime"]["state"] == "oversized_line_pending"
    assert journal.poll()["inserted_events"] == 0
    with source.open("a") as stream:
        stream.write("\n")
    append(paths["tenant-one"], {"type": "ui.argus", "text": "Recovered"})
    journal.poll()
    result = replay(journal)
    assert len(runtime(result)) == 1
    assert result["completeness"]["gap_events"] == 1
    assert result["sources"]["runtime"]["state"] == "caught_up"


def test_invalid_lines_advance_checkpoint_without_claiming_completion(setup):
    _, journal, _, paths = setup
    source = paths["tenant-one"] / "events.jsonl"
    source.write_bytes(b'broken\n[]\n{"type":"ui.argus","text":"\xff"}\n')
    journal.poll()
    result = replay(journal)
    assert result["completeness"]["gap_events"] == 3
    assert not runtime(result)
    assert journal.poll()["inserted_events"] == 0


def test_no_consent_no_capture_and_new_notice_requires_consent(setup):
    analytics, journal, _, paths = setup
    append(paths["tenant-two"], {"type": "ui.operator", "text": "UNCONSENTED"})
    assert journal.poll()["tenants_without_consent"] == 1
    with pytest.raises(AnalyticsError, match="consent_required"):
        journal.replay("tenant-two", "s-project")
    with pytest.raises(AnalyticsError, match="consent_required"):
        journal.append_user_event("tenant-two", "s-project", "feedback", {"text": "no"})
    with analytics._db() as db:
        assert db.execute("SELECT count(*) FROM journey_events WHERE tenant_id='tenant-two'").fetchone()[0] == 0
    analytics.notice_version = "new-notice"
    assert journal.poll()["projects"] == 0
    with pytest.raises(AnalyticsError, match="consent_required"):
        replay(journal)


@pytest.mark.parametrize("notice", ["operator-analytics-v2", "operator-analytics-v3-workspace"])
def test_v2_never_backfills_precollection_bytes_even_across_partial_lines_or_rotation(setup, notice):
    analytics, journal, _, paths = setup
    analytics.notice_version = notice
    source = paths["tenant-one"] / "events.jsonl"
    source.write_text('{"type":"ui.operator","text":"PRECONSENT"}\n{"type":"ui.argus","text":"OLD')
    assert journal.poll()["projects"] == 0
    analytics.record_consent("tenant-one", analytics.notice_version)
    journal.poll()
    with source.open("a") as stream:
        stream.write('"}\n{"type":"ui.argus","text":"AFTER_BOUNDARY"}\n')
    journal.poll()
    result = replay(journal)
    assert [row["payload"]["text"] for row in runtime(result)] == ["AFTER_BOUNDARY"]
    assert any(row["payload"].get("code") == "collection_boundary_no_backfill" for row in result["events"])
    source.rename(paths["tenant-one"] / "events.jsonl.1")
    source.write_text('{"type":"ui.argus","text":"REPLACEMENT_HISTORY"}\n')
    Journal(analytics).poll()
    result = replay(journal)
    assert "PRECONSENT" not in json.dumps(result) and "REPLACEMENT_HISTORY" not in json.dumps(result)
    assert result["completeness"]["complete"] is False


def test_cross_tenant_lookup_never_falls_back_to_other_project(setup):
    analytics, journal, _, paths = setup
    analytics.record_consent("tenant-two", analytics.notice_version)
    append(paths["tenant-one"], {"type": "ui.operator", "text": "TENANT_ONE_ONLY"})
    append(paths["tenant-two"], {"type": "ui.operator", "text": "TENANT_TWO_ONLY"})
    journal.poll()
    assert "TENANT_TWO_ONLY" not in json.dumps(replay(journal))
    other = journal.replay("tenant-two", "s-project")
    assert "TENANT_ONE_ONLY" not in json.dumps(other)
    with pytest.raises(AnalyticsError, match="tenant_not_found"):
        journal.replay("unconfigured", "s-project")
    with pytest.raises(AnalyticsError, match="invalid_session_id"):
        journal.replay("tenant-one", "../tenant-two")
    with pytest.raises(AnalyticsError, match="journal_not_found"):
        journal.delete_project("tenant-two", "only-in-another-tenant")
    journal.delete_project("tenant-one", "s-project")
    assert journal.replay("tenant-two", "s-project")["events"] == other["events"]


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo"])
def test_event_file_safety(setup, unsafe):
    _, journal, _, paths = setup
    source = paths["tenant-one"] / "events.jsonl"
    source.unlink()
    target = paths["tenant-two"] / "events.jsonl"
    append(paths["tenant-two"], {"type": "ui.argus", "text": "FORBIDDEN"})
    if unsafe == "symlink":
        source.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(target, source)
    else:
        os.mkfifo(source)
    result = journal.poll()
    assert result["errors"]
    result = replay(journal)
    assert not runtime(result)
    assert "FORBIDDEN" not in json.dumps(result)
    assert result["completeness"]["state"] == "incomplete"


def test_intermediate_project_symlink_not_followed(setup):
    _, journal, _, paths = setup
    first = paths["tenant-one"]
    moved = first.with_name("s-real")
    first.rename(moved)
    first.symlink_to(paths["tenant-two"], target_is_directory=True)
    result = journal.poll()
    assert result["discovery"][0]["skipped"] >= 1
    with pytest.raises(AnalyticsError, match="unsafe_path"):
        journal.append_user_event("tenant-one", "s-project", "feedback", {"text": "no"})


def test_redaction_private_channel_and_raw_tool_exclusion(setup):
    _, journal, _, paths = setup
    append(paths["tenant-one"],
           {"type": "ui.operator", "text": "Try sk-abcdefghijklmnop and password=not-for-export"},
           {"type": "ui.argus", "text": "Authorization: Bearer a-credential"},
           {"type": "agent.io.stream", "text": "RAW_PROVIDER_TEXT"},
           {"type": "role.session.turn", "text": "PRIVATE_CONTROL"},
           {"type": "engineer.progress", "kind": "reasoning", "text": "PRIVATE_REASON"},
           {"type": "engineer.progress", "kind": "agent_message",
            "data": {"channel": "analysis", "text": "NESTED_PRIVATE"}},
           {"type": "ui.argus", "text": '{"role":"thinking","text":"ENCODED_PRIVATE"}'},
           {"type": "engineer.progress", "kind": "tool_result", "tool_name": "shell",
            "status": "failed", "exit_code": 1, "text": "RAW_STDOUT",
            "summary": "RAW_SUMMARY", "output_excerpt": "RAW_EXCERPT",
            "command": "RAW_COMMAND", "stderr": "RAW_STDERR"},
           {"type": "life.mission.failed", "item_id": "task-a", "error_category": "timeout",
            "error": "UNBOUNDED_ERROR_DUMP", "raw_tool_output": "RAW_OUTPUT"})
    journal.poll()
    result = replay(journal)
    encoded = json.dumps(result)
    for forbidden in (
        "sk-abcdefghijklmnop", "not-for-export", "a-credential", "RAW_PROVIDER_TEXT",
        "PRIVATE_CONTROL", "PRIVATE_REASON", "NESTED_PRIVATE", "ENCODED_PRIVATE",
        "RAW_STDOUT", "RAW_SUMMARY", "RAW_EXCERPT", "RAW_COMMAND", "RAW_STDERR",
        "UNBOUNDED_ERROR_DUMP", "RAW_OUTPUT",
    ):
        assert forbidden not in encoded
    tool = next(event for event in runtime(result) if event["kind"] == "engineer.progress")
    assert tool["payload"]["exit_code"] == 1
    assert tool["payload"]["tool_name"] == "shell"
    assert runtime(result)[-1]["payload"]["error_category"] == "timeout"
    assert result["completeness"]["gap_events"] >= 5


def test_http_request_and_response_observations_are_separate_and_update_after_disconnect(setup):
    analytics, journal, _, paths = setup
    capture = Capture(
        analytics, "tenant-one", "s-project", "/api/projects/s-project/message",
        {"text": "Make the plot",
         "attachments": [{"filename": "data.csv", "contents": "DO_NOT_COPY_ATTACHMENT"}]},
    )
    append(paths["tenant-one"], {"type": "ui.operator", "text": "Make the plot"})
    journal.poll()
    first = replay(journal)
    assert len(first["events"]) == 2
    assert {event["source_kind"] for event in first["events"]} == {
        "runtime_event", "http_interaction",
    }
    assert first["sources"]["http"]["unfinished_observations"]
    assert journal.poll()["inserted_events"] == 0
    capture.feed(json.dumps({
        "kind": "task", "item_id": "explicit-task", "status": "running",
        "artifacts": [{"path": "output.svg", "contents": "NO_ARTIFACT_BYTES"}],
    }).encode())
    capture.finish(200, False, "application/json")
    assert journal.poll()["inserted_events"] == 1
    result = replay(journal)
    http = [event for event in result["events"] if event["source_kind"] == "http_interaction"]
    assert [event["source"]["phase"] for event in http] == ["request", "response"]
    assert http[1]["task_id"] == "explicit-task"
    assert http[1]["association"]["evidence"] == ["result.item_id"]
    assert http[1]["lifecycle"] is None
    assert http[1]["payload"]["state"] == "disconnected"
    assert http[1]["artifact_references"][0]["path"] == "output.svg"
    assert result["completeness"]["state"] == "incomplete"
    assert "DO_NOT_COPY_ATTACHMENT" not in json.dumps(result)
    assert "NO_ARTIFACT_BYTES" not in json.dumps(result)
    assert Journal(analytics).poll()["inserted_events"] == 0


def test_http_sse_reply_frames_and_bounds(setup):
    analytics, journal, _, _ = setup
    capture = Capture(
        analytics, "tenant-one", "s-project", "/api/projects/s-project/message/stream",
        {"text": "Hello"},
    )
    frames = [
        {"type": "phase", "label": "Working"},
        {"type": "delta", "text": "Visible reply"},
        {"type": "done", "result": {"kind": "chat", "reply": "Visible reply"}},
    ]
    capture.feed("".join("data: " + json.dumps(frame) + "\n\n" for frame in frames).encode())
    capture.finish(200, True, "text/event-stream")
    journal.poll()
    events = replay(journal)["events"]
    response = next(event for event in events if event["kind"] == "http.response")
    assert response["payload"]["frames"][1]["text"] == "Visible reply"
    assert response["payload"]["result"]["reply"] == "Visible reply"
    assert response["lifecycle"] is None
    assert not response["warnings"]


def test_user_observations_idempotency_conflicts_and_references(setup):
    _, journal, _, _ = setup
    first = journal.append_user_event(
        "tenant-one", "s-project", "correction", {"text": "Use seconds"},
        task_id="task-real", idempotency_key="submit-1",
    )
    second = journal.append_user_event(
        "tenant-one", "s-project", "correction", {"text": "Use seconds"},
        task_id="task-real", idempotency_key="submit-1",
    )
    assert first == second
    assert first["association"]["kind"] == "authoritative"
    with pytest.raises(AnalyticsError, match="idempotency_conflict"):
        journal.append_user_event(
            "tenant-one", "s-project", "feedback", {"text": "Changed"}, idempotency_key="submit-1",
        )
    download = journal.append_user_event(
        "tenant-one", "s-project", "download",
        {"artifacts": [{"path": "outputs/report.pdf?token=hidden", "contents": "PRIVATE_FILE"}]},
    )
    assert download["artifact_references"] == [{"path": "outputs/report.pdf"}]
    assert download["task_id"] is None
    assert "PRIVATE_FILE" not in json.dumps(download)
    with pytest.raises(AnalyticsError, match="private_payload"):
        journal.append_user_event(
            "tenant-one", "s-project", "feedback",
            {"text": "Private text", "channel": "analysis"},
        )


def test_delete_tombstone_prevents_reimport_across_restart_and_notice(setup):
    analytics, journal, _, paths = setup
    append(paths["tenant-one"], {"type": "ui.operator", "text": "Erase research copy"})
    capture = Capture(analytics, "tenant-one", "s-project", "/api/projects/s-project/message",
                      {"text": "Delete only journal, not source capture"})
    journal.poll()
    original = (paths["tenant-one"] / "events.jsonl").read_bytes()
    result = journal.delete_project("tenant-one", "s-project")
    assert result["events"] == 2
    assert result["checkpoints"] == 1
    assert result["http_observations"] == 1
    journal = Journal(analytics)
    assert journal.poll()["inserted_events"] == 0
    with pytest.raises(AnalyticsError, match="journal_deleted"):
        replay(journal)
    with pytest.raises(AnalyticsError, match="journal_deleted"):
        journal.append_user_event("tenant-one", "s-project", "feedback", {"text": "new"})
    analytics.notice_version = "next-policy"
    analytics.record_consent("tenant-one", analytics.notice_version)
    assert journal.poll()["inserted_events"] == 0
    assert journal.delete_project("tenant-one", "s-project")["events"] == 0
    assert (paths["tenant-one"] / "events.jsonl").read_bytes() == original
    with analytics._db() as db:
        assert db.execute("SELECT id FROM interactions WHERE id=?", (capture.id,)).fetchone()


@pytest.mark.parametrize("days", [1, 30, 365])
def test_retention_does_not_reimport_or_reset_sequences(setup, days):
    analytics, journal, now, paths = setup
    analytics.retention_days = days
    append(paths["tenant-one"], {"type": "ui.operator", "text": "old"})
    journal.poll()
    old_sequence = replay(journal)["next_sequence"]
    now[0] += min(days, 30) * 86400 + 1
    assert journal.prune()["events"] == 1
    assert Journal(analytics).poll()["inserted_events"] == 0
    result = replay(journal)
    assert result["events"] == []
    assert result["completeness"]["pruned_events"] == 1
    assert result["completeness"]["pruned_through_sequence"] == old_sequence
    append(paths["tenant-one"], {"type": "ui.argus", "text": "new"})
    journal.poll()
    assert replay(journal)["events"][0]["sequence"] > old_sequence


def test_capacity_pruning_retains_checkpoints_and_reports_loss(setup, monkeypatch):
    _, journal, _, paths = setup
    monkeypatch.setattr(journal_module, "MAX_RETAINED_EVENTS", 3)
    append(paths["tenant-one"], *[
        {"type": "ui.argus", "text": str(index)} for index in range(6)
    ])
    journal.poll()
    result = replay(journal)
    assert len(result["events"]) == 3
    assert result["completeness"]["pruned_events"] == 3
    assert journal.poll()["inserted_events"] == 0


def test_task_associations_never_use_nearby_lifecycle_as_authority(setup):
    _, journal, _, paths = setup
    append(paths["tenant-one"],
           {"type": "life.mission.started", "item_id": "real-1"},
           {"type": "ui.operator", "text": "A correction", "message_id": "ui-1"},
           {"type": "team.task", "item_id": "real-2", "task_id": "different-task"},
           {"type": "life.mission.completed", "item_id": "real-1"},
           {"type": "life.mission.completed", "item_id": "real-1",
            "status": "continue", "success": True},
           {"type": "life.mission.completed", "item_id": "real-1",
            "status": "done", "outcome": {"resumable": True}},
           {"type": "life.mission.completed", "item_id": "real-1",
            "status": "done", "success": False})
    journal.poll()
    events = runtime(replay(journal))
    assert events[1]["task_id"] is None
    assert events[2]["task_id"] is None
    assert events[2]["association"]["evidence"] == ["conflicting_task_ids"]
    assert [event["lifecycle"] for event in events[3:]] == [
        "settled_unknown", "continue", "incomplete", "incomplete",
    ]


def test_pagination_and_exact_output_bounds(setup):
    _, journal, _, paths = setup
    append(paths["tenant-one"], *[
        {"type": "ui.argus", "text": "z" * 12_000,
         "artifacts": [{"path": f"outputs/{index}.png", "label": "x" * 1000}]}
        for index in range(journal_module.MAX_BATCH_ROWS + 7)
    ])
    journal.poll()
    result = replay(journal)
    assert result["sources"]["runtime"]["state"] == "backlog"
    for _ in range(20):
        journal.poll()
    all_events, after = [], 0
    while True:
        result = replay(journal, after_sequence=after)
        assert len(json.dumps(result, ensure_ascii=True).encode()) <= journal_module.MAX_REPLAY_BYTES
        for event in result["events"]:
            assert len(json.dumps(event["payload"], ensure_ascii=True).encode()) <= journal_module.MAX_EVENT_BYTES
        all_events.extend(result["events"])
        after = result["next_sequence"]
        if not result["has_more"]:
            break
    assert len(all_events) == journal_module.MAX_BATCH_ROWS + 7
    assert len({event["id"] for event in all_events}) == len(all_events)
    assert all("payload_truncated" in event["warnings"] for event in all_events)
    tiny = replay(journal, limit=2)
    assert len(tiny["events"]) == 2
    assert tiny["has_more"]


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 501}, {"limit": True},
    {"after_sequence": -1}, {"after_sequence": True}, {"after_sequence": 2**63},
])
def test_invalid_replay_bounds(setup, kwargs):
    with pytest.raises(AnalyticsError):
        replay(setup[1], **kwargs)


def test_concurrent_polling_and_readers_are_idempotent(setup):
    analytics, journal, _, paths = setup
    append(paths["tenant-one"], *[
        {"type": "ui.argus", "text": str(index)} for index in range(30)
    ])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Journal(analytics).poll(), range(8)))
    assert sum(result["inserted_events"] for result in results) == 30
    expected = replay(journal)["events"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: replay(Journal(analytics)), range(8)))
    assert all(result["events"] == expected for result in results)
    assert len({event["sequence"] for event in expected}) == 30


def test_atomic_checkpoint_and_events_rollback_on_failure(setup, monkeypatch):
    analytics, journal, _, paths = setup
    append(paths["tenant-one"], {"type": "ui.argus", "text": "one"})
    original = journal._http

    def fail(*args):
        raise RuntimeError("simulated transaction interruption")

    monkeypatch.setattr(journal, "_http", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        journal.poll()
    with analytics._db() as db:
        assert db.execute("SELECT count(*) FROM journey_events").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM journey_projects").fetchone()[0] == 0
    monkeypatch.setattr(journal, "_http", original)
    assert Journal(analytics).poll()["inserted_events"] == 1


def test_source_missing_is_an_explicit_gap_not_completion(setup):
    _, journal, _, paths = setup
    append(paths["tenant-one"], {"type": "life.mission.started", "item_id": "real"})
    journal.poll()
    (paths["tenant-one"] / "events.jsonl").unlink()
    journal.poll()
    result = replay(journal)
    assert result["sources"]["runtime"]["state"] == "missing"
    assert result["events"][-1]["payload"]["code"] == "source_missing"
    assert [item["state"] for item in result["lifecycle"]] == ["start"]
    assert journal.poll()["inserted_events"] == 0


def test_rotation_during_bounded_read_rolls_back_and_reports_gap(setup, monkeypatch):
    _, journal, _, paths = setup
    path = paths["tenant-one"]
    append(path, {"type": "ui.argus", "text": "Do not commit mixed generation"})
    original = journal_module.os.pread
    changed = False

    def rotate(fd, size, offset):
        nonlocal changed
        raw = original(fd, size, offset)
        if size == journal_module.MAX_READ_BYTES and not changed:
            changed = True
            (path / "events.jsonl").rename(path / "events.jsonl.1")
            append(path, {"type": "ui.argus", "text": "new generation"})
        return raw

    monkeypatch.setattr(journal_module.os, "pread", rotate)
    assert journal.poll()["errors"][0]["code"] == "source_changed_during_read"
    result = replay(journal)
    assert not runtime(result)
    assert result["events"][0]["kind"] == "journal.gap"
    assert journal.poll()["inserted_events"] == 1
    assert runtime(replay(journal))[0]["payload"]["text"] == "new generation"


def test_http_oversized_body_and_batch_limit_are_explicit(setup):
    analytics, journal, _, _ = setup
    for _ in range(journal_module.MAX_HTTP_ROWS + 1):
        capture = Capture(
            analytics, "tenant-one", "s-project", "/api/projects/s-project/message",
            {"text": "x" * (journal_module.MAX_HTTP_BYTES + 10)},
        )
        capture.feed(json.dumps({"kind": "chat", "reply": "done"}).encode())
        capture.finish(200, True, "application/json")
    journal.poll()
    result = replay(journal)
    assert result["sources"]["http"]["pending"]
    assert len(result["events"]) == journal_module.MAX_HTTP_ROWS * 2
    assert any("http_body_oversized" in event["warnings"] for event in result["events"])
    journal.poll()
    assert not replay(journal)["sources"]["http"]["pending"]
    assert journal.poll()["inserted_events"] == 0


def test_current_consent_revocation_blocks_reads_but_not_deletion(setup):
    analytics, journal, _, paths = setup
    append(paths["tenant-one"], {"type": "ui.argus", "text": "previously consented"})
    journal.poll()
    with analytics._db() as db:
        db.execute("DELETE FROM consents WHERE tenant_id='tenant-one'")
    assert journal.poll()["projects"] == 0
    with pytest.raises(AnalyticsError, match="consent_required"):
        replay(journal)
    assert journal.delete_project("tenant-one", "s-project")["events"] == 1


def test_user_payload_limits_and_runtime_ids_remain_exact(setup):
    _, journal, _, paths = setup
    with pytest.raises(AnalyticsError, match="user_payload_too_large"):
        journal.append_user_event(
            "tenant-one", "s-project", "correction", {"text": "x" * 50_000},
        )
    with pytest.raises(AnalyticsError, match="invalid_user_event_kind"):
        journal.append_user_event("tenant-one", "s-project", [], {"text": "x"})
    append(paths["tenant-one"], {
        "type": "ui.argus", "text": "long " * 3000, "item_id": "exact-runtime-task-id",
    })
    journal.poll()
    event = runtime(replay(journal))[0]
    assert event["task_id"] == "exact-runtime-task-id"
    assert event["association"]["kind"] == "authoritative"
    assert "payload_truncated" in event["warnings"]


@pytest.mark.parametrize("stream", [False, True])
def test_real_message_http_contract_links_input_response_and_runtime(setup, monkeypatch, stream):
    from fastapi.testclient import TestClient

    from argus_skill.life.memory import BacklogItem
    from argus_skill.webapi import manager_bridge, manager_pending_question, server
    from argus_skill.webapi.manager_dispatch import _item_to_dict

    analytics, journal, _, paths = setup
    path = paths["tenant-one"]
    task = BacklogItem.new(title="Synthetic task", objective="Make a plot", item_id="task-http-contract")
    result = {"kind": "task", "reply": None, "item": _item_to_dict(task, "Synthetic task")}
    monkeypatch.setattr(manager_bridge, "manager_message", lambda *args, **kwargs: dict(result))
    monkeypatch.setattr(server, "start_project_daemon", lambda *args, **kwargs: {"alive": True})
    monkeypatch.setattr(manager_pending_question, "record_task_dispatch_ack", lambda *args, **kwargs: None)
    route = "/api/projects/s-project/message" + ("/stream" if stream else "")
    captured = Capture(analytics, "tenant-one", "s-project", route, {"text": "Make a plot"})
    journal.poll()
    initial = next(event for event in replay(journal)["events"] if event["kind"] == "http.request")
    assert initial["task_id"] is None
    with TestClient(server.create_app(global_root=path.parent.parent)) as client:
        response = client.post(route, json={"text": "Make a plot"})
    assert response.status_code == 200
    for offset in range(0, len(response.content), 11):
        captured.feed(response.content[offset:offset + 11])
    captured.finish(response.status_code, True, response.headers["content-type"])
    append(path, {"type": "life.mission.started", "item_id": task.id})
    journal.poll()
    events = replay(journal)["events"]
    request = next(event for event in events if event["kind"] == "http.request")
    response_event = next(event for event in events if event["kind"] == "http.response")
    runtime_event = next(event for event in events if event["kind"] == "life.mission.started")
    assert request["task_id"] == response_event["task_id"] == runtime_event["task_id"] == task.id
    assert request["id"] == initial["id"] and request["sequence"] == initial["sequence"]
    assert request["payload"]["input"]["text"] == "Make a plot"
    assert request["association"]["response_event_id"] == response_event["id"]
    assert request["association"]["interaction_id"] == response_event["association"]["interaction_id"] == captured.id
    assert response_event["association"]["evidence"] == ["result.item.id"]
    assert response_event["payload"]["result"]["item"]["id"] == task.id
    assert analytics.dashboard()["accounts"][0]["accepted_task_requests"] == 1
    assert Journal(analytics).poll()["inserted_events"] == 0


def test_projection_upgrade_repairs_retained_links_without_reimport(setup):
    analytics, journal, _, _ = setup
    captured = Capture(analytics, "tenant-one", "s-project", "/api/projects/s-project/message", {"text": "Synthetic"})
    captured.feed(b'{"kind":"task","item":{"id":"old-explicit-task","status":"pending"}}')
    captured.finish(200, True, "application/json")
    journal.poll()
    before = replay(journal)
    with analytics._db() as db:
        for row in db.execute("SELECT sequence,record FROM journey_events").fetchall():
            event = json.loads(row["record"])
            event["task_id"] = None
            event["association"] = {"kind": "unassigned", "evidence": ["no_explicit_task_id"]}
            if event["kind"] == "http.response":
                event["payload"]["result"].pop("item")
            db.execute("UPDATE journey_events SET record=? WHERE sequence=?", (json.dumps(event), row["sequence"]))
        db.execute("ALTER TABLE journey_http DROP COLUMN projection_version")
    upgraded = Journal(analytics)
    assert upgraded.poll()["inserted_events"] == 0
    after = replay(upgraded)
    assert [event["id"] for event in after["events"]] == [event["id"] for event in before["events"]]
    assert all(event["task_id"] == "old-explicit-task" for event in after["events"])
    with analytics._db() as db:
        db.execute("DELETE FROM journey_events")
        db.execute("UPDATE journey_http SET projection_version=0")
    assert upgraded.poll()["inserted_events"] == 0
    assert replay(upgraded)["events"] == []
