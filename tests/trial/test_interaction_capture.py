"""Synthetic, local SQLite coverage for observable interaction capture."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.interaction_capture import (
    MAX_BYTES,
    Capture,
    get_interaction,
    list_interactions,
    prune_interactions,
)


@pytest.fixture
def analytics(tmp_path):
    result = Analytics(
        tmp_path / "index",
        {
            "tenant-a": {"data_dir": str(tmp_path / "a"), "internal_test": False},
            "tenant-b": {"data_dir": str(tmp_path / "b"), "internal_test": False},
        },
        tmp_path / "unused-meter",
        tmp_path / "unused-compute",
        notice_version="capture-v1",
        clock=lambda: 2_000_000_000,
    )
    for tenant in result.tenants:
        result.record_consent(tenant, result.notice_version)
    return result


def capture(analytics, input_data=None, stream=True, tenant="tenant-a"):
    return Capture(
        analytics, tenant, "s-one",
        "/api/projects/s-one/message" + ("/stream" if stream else ""),
        {"text": "Run the requested study"} if input_data is None else input_data,
    )


def frame(value):
    return ("data: " + json.dumps(value) + "\n\n").encode()


def record(analytics, item):
    return get_interaction(analytics, "tenant-a", item.id, include_trace=False)


def test_invocation_commits_sanitized_user_input_immediately(analytics):
    item = capture(analytics, {
        "text": "Explain reasoning clearly. api_key=synthetic-secret\nKeep my question.",
        "attachments": [{"attachment_id": "attachment-1", "Authorization": "private-auth"}],
        "route_override": "task",
        "command": "echo hello",
        "name": "Study",
        "resources": {"gpu": 1, "password": "private-password"},
        "Cookie": "private-cookie",
        "Authorization": "private-header",
        "provider_request": "private-provider",
    })
    found = record(analytics, item)
    assert type(found["id"]) is int
    assert found["notice_version"] == "capture-v1"
    assert found["sid"] == "s-one"
    assert found["created_at"] == 2_000_000_000
    assert found["finished_at"] is None
    assert found["state"] == found["outcome"] == "incomplete"
    assert found["result"] is None
    assert "Explain reasoning clearly." in found["input"]["text"]
    assert found["input"]["attachments"][0]["attachment_id"] == "attachment-1"
    with analytics._db() as db:
        dump = "\n".join(db.iterdump())
    for secret in ("synthetic-secret", "private-auth", "private-password",
                   "private-cookie", "private-header", "private-provider"):
        assert secret not in dump


def test_exact_version_consent_required_before_any_capture(analytics):
    analytics.notice_version = "capture-v2"
    for operation in (
        lambda: capture(analytics),
        lambda: list_interactions(analytics, "tenant-a"),
        lambda: get_interaction(analytics, "tenant-a", 1),
    ):
        with pytest.raises(AnalyticsError) as exc:
            operation()
        assert exc.value.status == 403
    with analytics._db() as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='interactions'"
        ).fetchone() is None


def test_json_chat_result_matches_its_input_and_finish_is_idempotent(analytics):
    item = capture(analytics, {"text": "What is two plus two?"}, stream=False)
    body = json.dumps({"kind": "chat", "reply": "Four.", "raw_provider_stream": "OMIT"}).encode()
    item.feed(body[:9])
    item.feed(body[9:])
    item.finish(200, True, "application/json; charset=utf-8")
    item.feed(b"ignored after finish")
    item.finish(500, False, "text/plain")
    found = record(analytics, item)
    assert found["input"] == {"text": "What is two plus two?"}
    assert found["result"] == {"kind": "chat", "reply": "Four."}
    assert found["outcome"] == "chat_response"
    assert found["state"] == "response_complete"
    assert found["error_code"] is None
    assert len(item._buffer) == 0


def test_sse_public_frames_and_real_declared_task_ids_not_mission_completion(analytics):
    item = capture(analytics)
    values = [
        {"type": "phase", "role": "manager", "label": "Dispatching"},
        {"type": "delta", "text": "Queued.", "fragment_mode": "snapshot"},
        {"type": "done", "result": {
            "kind": "task", "reply": None,
            "item": {"id": "backlog-actual-42", "status": "pending"},
            "task_ids": ["actual-task-a", "actual-task-b"],
            "root_task_id": "actual-root",
            "dispatch_state": "queued",
        }},
    ]
    body = b": heartbeat\n\n" + b"".join(frame(value) for value in values)
    for offset in range(0, len(body), 7):
        item.feed(body[offset:offset + 7])
    item.finish(200, True, "text/event-stream")
    found = record(analytics, item)
    assert found["frames"] == values
    assert found["result"] == values[-1]["result"]
    assert found["sid"] == "s-one"
    assert found["outcome"] == "background_task_dispatched"
    assert "mission_completed" not in json.dumps(found)
    assert found["state"] == "response_complete"


@pytest.mark.parametrize("value", [
    {"type": "reasoning", "text": "PRIVATE-COT"},
    {"type": "agent.io.stream", "text": "PRIVATE-PROVIDER"},
    {"type": "delta", "channel": "analysis", "reasoning": "PRIVATE-COT"},
    {"type": "delta", "channel": "analysis", "text": "PRIVATE-COT"},
    {"type": "delta", "role": "thinking", "text": "PRIVATE-COT"},
    {"type": "delta", "result": {"analysis": "PRIVATE-COT"}},
    {"type": "delta", "text": "<thinking>PRIVATE-COT</thinking>"},
    {"type": "delta", "text": "<think>PRIVATE-COT</think>"},
    {"type": "delta", "result": {"chain_of_thought": "PRIVATE-COT"}},
    {"type": "delta", "text": '{"type":"reasoning","text":"PRIVATE-COT"}'},
    {"type": "delta", "text": '{"channel":"analysis","text":"PRIVATE-COT"}'},
    {"type": "delta", "text": '{"type":"agent.io.stream","text":"PRIVATE-PROVIDER"}'},
])
def test_no_private_reasoning_or_raw_provider_frames(analytics, value):
    item = capture(analytics)
    item.feed(frame(value))
    item.feed(frame({"type": "done", "result": {"kind": "chat", "reply": "Public reply"}}))
    item.finish(200, True, "text/event-stream")
    found = record(analytics, item)
    assert len(found["frames"]) == 1
    assert found["suppressed_frames"] == 1
    with analytics._db() as db:
        assert "PRIVATE-" not in "\n".join(db.iterdump())


def test_wrapped_phase_delta_payloads_and_response_secrets(analytics):
    item = capture(analytics)
    item.feed(frame({"type": "phase", "result": {"label": "Routing"}}))
    item.feed(frame({"type": "delta", "result": {"text": "Visible progress"}}))
    item.feed(frame({"type": "done", "result": {
        "kind": "chat", "reply": "Bearer synthetic-response-secret",
        "Authorization": "private", "raw_provider_stream": "PRIVATE-PROVIDER",
    }}))
    item.finish(200, True, "text/event-stream")
    found = record(analytics, item)
    assert found["frames"][0]["result"]["label"] == "Routing"
    assert found["frames"][1]["result"]["text"] == "Visible progress"
    assert "REDACTED" in found["result"]["reply"]
    assert "synthetic-response-secret" not in found["result"]["reply"]
    assert "PRIVATE-PROVIDER" not in json.dumps(found)


@pytest.mark.parametrize(
    "body,status,complete,media,state,outcome,error",
    [
        (b"", 200, False, "text/event-stream", "disconnected", "incomplete", "missing_final_result"),
        (b"data: {}\n\n", 200, True, "text/event-stream",
         "response_complete", "incomplete", "missing_final_result"),
        (b"data: {bad}\n\n", 200, True, "text/event-stream",
         "response_complete", "incomplete", "invalid_sse_json"),
        (b"data: {}", 200, True, "text/event-stream",
         "response_complete", "incomplete", "incomplete_sse_frame"),
        (b"{bad}", 200, True, "application/json",
         "response_complete", "incomplete", "invalid_response_json"),
        (b"{}", 200, True, "application/json",
         "response_complete", "incomplete", "missing_public_result"),
        (b"[]", 200, True, "application/json",
         "response_complete", "incomplete", "missing_public_result"),
        (b'{"kind":"chat"}', 200, True, "application/json",
         "response_complete", "incomplete", "missing_final_reply"),
        (b"private raw response", 200, True, "text/plain",
         "response_complete", "incomplete", "unsupported_content_type"),
        (b'{"error":"unavailable"}', 503, True, "application/json",
         "response_error", "response_error", "response_error"),
        (b'{"kind":"error","reply":"Failed"}', 200, True, "application/json",
         "response_error", "response_error", "response_error"),
        (frame({"type": "error", "error": "Failed", "diagnostic": "PRIVATE"}), 200, True,
         "text/event-stream", "response_error", "response_error", "stream_error"),
        (frame({"type": "done", "result": {"kind": "error", "reply": "Failed"}}), 200, True,
         "text/event-stream", "response_error", "response_error", "stream_error"),
    ],
)
def test_explicit_transport_and_observation_failure_states(
    analytics, body, status, complete, media, state, outcome, error,
):
    item = capture(analytics)
    item.feed(body)
    item.finish(status, complete, media)
    found = record(analytics, item)
    assert found["state"] == state
    assert found["outcome"] == outcome
    assert found["error_code"] == error
    assert "PRIVATE" not in json.dumps(found)


def test_error_after_done_overrides_success_and_disconnect_does_not_invent_success(analytics):
    done = frame({"type": "done", "result": {"kind": "chat", "reply": "Observed"}})
    first = capture(analytics)
    first.feed(done + frame({"type": "error", "error": "Transport failed"}))
    first.finish(200, True, "text/event-stream")
    assert record(analytics, first)["outcome"] == "response_error"
    second = capture(analytics)
    second.feed(done)
    second.finish(200, False, "text/event-stream")
    found = record(analytics, second)
    assert found["outcome"] == "incomplete"
    assert found["state"] == "disconnected"
    assert found["result"]["reply"] == "Observed"


def test_input_and_response_caps_and_missing_final_are_explicit(analytics):
    item = capture(analytics, {"text": "x" * (MAX_BYTES * 2)})
    found = record(analytics, item)
    assert found["input_truncated"]
    assert len(json.dumps(found["input"]).encode()) <= MAX_BYTES
    item.feed(b"data: " + b"x" * (MAX_BYTES * 2))
    assert len(item._buffer) == MAX_BYTES
    item.feed(b"x" * MAX_BYTES)
    assert len(item._buffer) == MAX_BYTES
    item.finish(200, True, "text/event-stream")
    found = record(analytics, item)
    assert found["response_truncated"]
    assert found["outcome"] == "incomplete"
    assert found["result"] is None
    assert found["error_code"]


def test_truncation_after_valid_done_still_is_not_capture_success(analytics):
    item = capture(analytics)
    item.feed(frame({"type": "done", "result": {"kind": "chat", "reply": "Observed"}}))
    item.feed(b": heartbeat\n\n" * MAX_BYTES)
    item.finish(200, True, "text/event-stream")
    found = record(analytics, item)
    assert found["response_truncated"]
    assert found["outcome"] == "incomplete"
    assert found["error_code"]


def test_cross_tenant_lookup_is_scoped_and_version_changes_hide_old_rows(analytics):
    first = capture(analytics)
    second = capture(analytics, tenant="tenant-b")
    assert [r["id"] for r in list_interactions(analytics, "tenant-a")["interactions"]] == [first.id]
    with pytest.raises(AnalyticsError) as exc:
        get_interaction(analytics, "tenant-a", second.id)
    assert exc.value.status == 404
    analytics.notice_version = "capture-v2"
    analytics.record_consent("tenant-a", analytics.notice_version)
    assert list_interactions(analytics, "tenant-a") == {"interactions": []}
    with pytest.raises(AnalyticsError) as exc:
        get_interaction(analytics, "tenant-a", first.id)
    assert exc.value.status == 404
    with pytest.raises(AnalyticsError) as exc:
        first.finish(200, True, "application/json")
    assert exc.value.status == 403


def test_restart_preserves_unfinished_invocation(analytics):
    item = capture(analytics)
    item.feed(frame({"type": "delta", "text": "Not yet committed"}))
    restarted = Analytics(
        analytics.path.parent, analytics.tenants, analytics.trial_db, analytics.compute_db,
        notice_version=analytics.notice_version,
    )
    found = record(restarted, item)
    assert found["state"] == found["outcome"] == "incomplete"
    assert found["finished_at"] is None
    assert found["result"] is None
    assert found["frames"] == []
    assert capture(restarted).id > item.id


def test_current_project_trace_and_explicit_unavailable_errors(analytics):
    item = capture(analytics)
    found = get_interaction(analytics, "tenant-a", item.id)
    assert found["project_trace"]["state"] == "error"
    assert found["project_trace"]["status"] == 404
    assert found["project_trace"]["code"]
    root = analytics.tenants["tenant-a"]["data_dir"] / "home/.argus-skill/projects/s-one"
    root.mkdir(parents=True)
    (root / "session.json").write_text(json.dumps({"id": "s-one", "display_name": "Study"}))
    (root / "backlog.jsonl").write_text('{"id":"task-actual","status":"pending"}\n')
    trace = get_interaction(analytics, "tenant-a", item.id)["project_trace"]
    assert trace["sid"] == "s-one"
    assert trace["rows"][0]["data"]["id"] == "task-actual"
    (root / "backlog.jsonl").write_text('{"id":"task-actual","status":"done"}\n')
    trace = get_interaction(analytics, "tenant-a", item.id)["project_trace"]
    assert trace["rows"][0]["data"]["status"] == "done"
    assert "project_trace" not in record(analytics, item)
    assert record(analytics, item)["outcome"] == "incomplete"


def test_trace_exception_does_not_leak_diagnostic(analytics, monkeypatch):
    item = capture(analytics)

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE-CREDENTIAL")

    monkeypatch.setattr(analytics, "trace", unavailable)
    found = get_interaction(analytics, "tenant-a", item.id)
    assert found["project_trace"] == {"state": "error", "code": "project_trace_unavailable"}
    assert "PRIVATE" not in json.dumps(found)


def test_retention_only_deletes_expired_interactions(analytics):
    old = capture(analytics)
    boundary = capture(analytics)
    fresh = capture(analytics)
    cutoff = analytics.clock() - analytics.retention_days * 86400
    with analytics._db() as db:
        db.execute("UPDATE interactions SET created_at=? WHERE id=?", (cutoff - 1, old.id))
        db.execute("UPDATE interactions SET created_at=? WHERE id=?", (cutoff, boundary.id))
        before_consents = [tuple(r) for r in db.execute("SELECT * FROM consents")]
    project = analytics.tenants["tenant-a"]["data_dir"]
    project.mkdir()
    source = project / "fixture.json"
    source.write_text('{"untouched":true}')
    assert prune_interactions(analytics) == 1
    assert {r["id"] for r in list_interactions(analytics, "tenant-a")["interactions"]} == {
        boundary.id, fresh.id,
    }
    assert source.read_text() == '{"untouched":true}'
    with analytics._db() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM consents")] == before_consents


def test_parallel_capture_transactions_and_summary_contract(analytics):
    def run(index):
        item = capture(analytics, {"text": f"Question {index}"}, stream=False)
        item.feed(json.dumps({"kind": "chat", "reply": f"Reply {index}"}).encode())
        item.finish(200, True, "application/json")
        return item.id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(run, range(18)))
    assert len(set(ids)) == 18
    summaries = list_interactions(analytics, "tenant-a", limit=3)["interactions"]
    assert len(summaries) == 3
    assert [row["id"] for row in summaries] == sorted(ids, reverse=True)[:3]
    assert all("input" not in row and "frames" not in row and "result" not in row for row in summaries)
    for id in ids:
        found = get_interaction(analytics, "tenant-a", id, include_trace=False)
        assert found["result"]["reply"].split()[-1] == found["input"]["text"].split()[-1]


@pytest.mark.parametrize("limit", [0, 501, True, "10"])
def test_list_limit_validation(analytics, limit):
    with pytest.raises(AnalyticsError) as exc:
        list_interactions(analytics, "tenant-a", limit)
    assert exc.value.status == 400


def test_multiline_crlf_sse_and_json_byte_input(analytics):
    item = capture(analytics, b'{"text":"Question","Authorization":"DO-NOT-STORE"}')
    item.feed(
        b'data: {"type":"done",\r\ndata: "result":{"kind":"chat","reply":"Answer"}}\r\n\r\n'
    )
    item.finish(200, True, "text/event-stream; charset=utf-8")
    found = record(analytics, item)
    assert found["input"] == {"text": "Question"}
    assert found["result"] == {"kind": "chat", "reply": "Answer"}
    assert found["outcome"] == "chat_response"


def test_large_json_input_and_response_never_parse_partial_payloads(analytics):
    item = capture(analytics, b'{"text":"' + b"x" * MAX_BYTES + b'"}', stream=False)
    item.feed(b'{"kind":"chat","reply":"' + b"x" * MAX_BYTES + b'"}')
    item.finish(200, True, "application/json")
    found = record(analytics, item)
    assert found["input_truncated"]
    assert found["input"] == {"capture_error": "input_truncated"}
    assert found["response_truncated"]
    assert found["result"] is None
    assert found["outcome"] == "incomplete"
    assert found["error_code"] == "response_truncated"


def test_no_project_trace_and_query_content_are_not_invented_or_stored(analytics):
    item = Capture(
        analytics, "tenant-a", None, "/compute/jobs?token=NEVER-STORE",
        {"command": "echo done", "resources": {"gpu": 1}},
    )
    item.feed(b'{"id":42,"status":"queued"}')
    item.finish(202, True, "application/json")
    found = get_interaction(analytics, "tenant-a", item.id)
    assert found["path"] == "/compute/jobs"
    assert found["result"] == {"id": 42, "status": "queued"}
    assert found["outcome"] == "response_observed"
    assert found["project_trace"] == {"state": "unavailable", "code": "no_project_id"}
    assert "NEVER-STORE" not in json.dumps(found)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("result,expected", [
    ({"kind": "task", "item": {"id": "task-real-1", "status": "pending"}}, True),
    ({"kind": "task", "item": {"id": "task-real-1"}, "dispatch_state": "queued_after_current"}, True),
    ({"kind": "task", "item": {"id": "task-real-1"}, "duplicate": True}, False),
    ({"kind": "task", "item": None, "dispatch_state": "planner_pending"}, False),
    ({"kind": "task", "item": {"id": "../unsafe"}}, False),
    ({"kind": "task", "task_id": "task-a", "item": {"id": "task-b"}}, False),
    ({"kind": "chat", "reply": "Hello"}, False),
    ({"kind": "error", "reply": "Nothing queued"}, False),
])
def test_validated_task_acceptance_requires_response_evidence(analytics, stream, result, expected):
    captured = capture(analytics, stream=stream)
    captured.feed(frame({"type": "done", "result": result}) if stream else json.dumps(result).encode())
    captured.finish(200, True, "text/event-stream" if stream else "application/json")
    found = record(analytics, captured)
    assert found["task_accepted"] is expected
    if expected:
        assert found["task_id"] == "task-real-1"
        assert found["outcome"] == "background_task_dispatched"
    elif result.get("kind") == "task":
        assert found["outcome"] == "task_dispatch_unverified"
    assert list_interactions(analytics, "tenant-a")["interactions"][0]["task_accepted"] is expected


def test_direct_task_endpoint_uses_item_id_without_kind(analytics):
    captured = Capture(analytics, "tenant-a", "s-one", "/api/projects/s-one/tasks", {"text": "Synthetic"})
    captured.feed(b'{"item":{"id":"task-direct","status":"pending"}}')
    captured.finish(200, True, "application/json")
    assert record(analytics, captured)["task_accepted"] is True
    assert record(analytics, captured)["task_id"] == "task-direct"


def test_existing_retained_capture_metadata_is_upgraded(analytics):
    captured = capture(analytics, stream=False)
    captured.feed(b'{"kind":"task","item":{"id":"task-old","status":"pending"}}')
    captured.finish(200, True, "application/json")
    with analytics._db() as db:
        db.execute("DROP INDEX interactions_task_acceptance")
        db.execute("ALTER TABLE interactions DROP COLUMN task_id")
        db.execute("ALTER TABLE interactions DROP COLUMN task_accepted")
    found = record(analytics, captured)
    assert found["task_id"] == "task-old"
    assert found["task_accepted"] is True
    assert found["id"] == captured.id
