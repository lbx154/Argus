"""Synthetic retained-journal fixtures only; never connect to live trial stores."""
import hashlib
import io
import json
import subprocess
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus_skill.trial import training_data as module
from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.interaction_capture import Capture
from argus_skill.trial.journey_journal import Journal
from argus_skill.trial.research_controls import ResearchControls
from argus_skill.trial.store import Store
from argus_skill.trial.training_capture import PI_EXTENSION_SOURCE
from argus_skill.trial.training_data import COMBINED_NOTICE_VERSION, NOTICE_VERSION, TrainingData
from argus_skill.trial.training_routes import register_training_routes


@pytest.fixture
def training(tmp_path):
    now = [2_000_000_000.0]
    analytics = Analytics(
        tmp_path / "analytics",
        {tenant: {"data_dir": str(tmp_path / tenant), "internal_test": False}
         for tenant in ("tenant-one", "tenant-two")},
        tmp_path / "trial.sqlite3", tmp_path / "compute.sqlite3", clock=lambda: now[0],
    )
    store = Store(analytics.trial_db, clock=lambda: now[0])
    for tenant, config in analytics.tenants.items():
        with store.transaction() as db:
            db.execute("INSERT INTO trial_keys(key_id,credential_hash) VALUES (?,?)", (tenant, tenant))
        analytics.record_consent(tenant, analytics.notice_version)
        path = config["data_dir"] / "home/.argus-skill/projects/s-project"
        path.mkdir(parents=True)
        (path / "session.json").write_text(json.dumps({"id": "s-project"}))
        (path / "events.jsonl").touch()
    journal = Journal(analytics)
    controls = ResearchControls(analytics)
    return TrainingData(analytics, journal, controls), store, now


def grant(data, tenant="tenant-one", *, internal=True, external=False):
    return data.set_permissions(tenant, {
        "internal_training": internal, "external_sharing": external, "notice_version": NOTICE_VERSION,
    })


def selection(tenant="tenant-one"):
    return [{"tenant_id": tenant, "sid": "s-project"}]


def chat(training, *, tenant="tenant-one", text="Explain sorting.", reply="Sorting orders items.",
         task=None):
    data, _, now = training
    now[0] += 1
    capture = Capture(data.analytics, tenant, "s-project", "/api/projects/s-project/message", {"text": text})
    now[0] += 1
    result = {"kind": "chat", "reply": reply}
    if task:
        result["task_id"] = task
    capture.feed(json.dumps(result).encode())
    capture.finish(200, True, "application/json")
    data.journal.poll(tenant)
    return capture


def unpack(data, purpose="internal_training", projects=None, review=None):
    blob, filename = data.export(
        purpose, projects or selection(), review=review or {"content_approved": True},
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return files, filename


def records(files, name):
    return [json.loads(row) for row in files[name].splitlines()]


def test_independent_consent_version_revoke_regrant_and_no_retroactive_content(training):
    data, _, now = training
    assert data.permissions("tenant-one")["internal_training"] is False
    chat(training)
    assert data.preview("internal_training", selection())["projects"][0]["reason"] == "purpose_consent_required"
    grant(data)
    grant_time = data.permissions("tenant-one")["granted_at"]["internal_training"]
    assert data.permissions("tenant-one")["external_sharing"] is False
    now[0] += 1
    grant(data)
    assert data.permissions("tenant-one")["granted_at"]["internal_training"] == grant_time
    assert not data.preview("internal_training", selection())["candidates"]
    chat(training, text="New question.")
    assert len(data.preview("internal_training", selection())["candidates"]) == 1
    grant(data, internal=False)
    assert not data.preview("internal_training", selection())["projects"][0]["eligible"]
    now[0] += 1
    grant(data, external=True)
    assert not data.preview("internal_training", selection())["candidates"]
    with pytest.raises(AnalyticsError, match="notice_version"):
        data.set_permissions("tenant-one", {
            "notice_version": "old", "internal_training": True, "external_sharing": True,
        })
    with pytest.raises(ValueError, match="booleans"):
        data.set_permissions("tenant-one", {
            "notice_version": NOTICE_VERSION, "internal_training": 1, "external_sharing": False,
        })
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET notice_version='retired'")
    assert not data.permissions("tenant-one")["internal_training"]
    assert not data.preview("external_sharing", selection())["projects"][0]["eligible"]


def test_real_capture_reviewed_chat_zip_hashes_dedup_and_empty_validation(training):
    data, _, _ = training
    grant(data)
    chat(training)
    chat(training)
    preview = data.preview("internal_training", selection())
    assert len(preview["candidates"]) == 2
    assert preview["counts"]["sft"] == 0
    assert preview["counts"]["duplicates"] == 1
    ids = [c["event_id"] for c in preview["candidates"]]
    files, filename = unpack(data, review={"content_approved": True, "approved_event_ids": ids})
    assert filename.startswith("argus-internal_training-") and filename.endswith("-sft-1.zip")
    assert all("/" not in name and "\\" not in name and ".." not in name for name in files)
    assert {"manifest.json", "quality_report.json", "README.txt", "trajectories.jsonl",
            "provenance.jsonl", "sft_train.jsonl", "hf_trl_train.jsonl"} <= files.keys()
    samples = records(files, "sft_train.jsonl")
    assert samples == [{"messages": [{"role": "user", "content": "Explain sorting."},
                                     {"role": "assistant", "content": "Sorting orders items."}]}]
    assert files["sft_validation.jsonl"] == b""
    assert files["hf_trl_train.jsonl"] == files["sft_train.jsonl"]
    manifest = json.loads(files["manifest.json"])
    for name, metadata in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == metadata["sha256"]
        assert len(files[name]) == metadata["bytes"]
    assert manifest["rights_status"] == "unknown" and not manifest["legal_certification"]
    later_only, _ = unpack(data, review={"content_approved": True, "approved_event_ids": [ids[-1]]})
    assert len(records(later_only, "sft_train.jsonl")) == 1
    trajectories = records(files, "trajectories.jsonl")
    assert {c["event_id"] for c in preview["candidates"]} <= {e["id"] for e in trajectories}
    assert all(not sample["global_complete"] for sample in records(files, "samples.jsonl"))
    assert "Sorting orders items." not in json.dumps(data.controls.audit_log())
    with data.analytics._db() as db:
        assert db.execute("SELECT max(sft) FROM training_audit_counts").fetchone()[0] == 1


def test_explicit_feedback_not_download_and_sharing_review_gate(training):
    data, _, now = training
    grant(data, external=True)
    chat(training, task="task-real")
    now[0] += 1
    data.journal.append_user_event("tenant-one", "s-project", "download", {"filename": "report.pdf"},
                                   task_id="task-real")
    assert data.preview("internal_training", selection())["counts"]["sft"] == 0
    data.controls.feedback("tenant-one", "s-project", {"task_id": "task-real", "verdict": "met_need"})
    assert data.preview("internal_training", selection())["counts"]["sft"] == 1
    with pytest.raises(AnalyticsError, match="content_review_required"):
        data.export("internal_training", selection())
    with pytest.raises(AnalyticsError, match="rights_review_required"):
        unpack(data, "external_sharing")
    files, _ = unpack(data, "external_sharing", review={"content_approved": True, "rights_reviewed": True})
    assert json.loads(files["manifest.json"])["explicit_operator_rights_review"] is True
    assert json.loads(files["manifest.json"])["provider_license_review"] == "pending"
    now[0] += 1
    data.controls.feedback("tenant-one", "s-project", {"task_id": "task-real", "verdict": "needs_changes"})
    preview = data.preview("internal_training", selection())
    assert preview["counts"]["sft"] == 0
    assert preview["reason_counts"]["human_requested_changes"] == 1
    assert any(row["outcome"] == "failed" for row in data.controls.audit_log()["events"])


def test_disabled_expired_deleted_and_retention_exclude_copies(training):
    data, store, now = training
    grant(data)
    chat(training)
    store.set_access("tenant-one", enabled=False)
    files, _ = unpack(data)
    assert records(files, "trajectories.jsonl") == []
    assert json.loads(files["quality_report.json"])["reason_counts"]["tester_access_inactive"] == 1
    store.set_access("tenant-one", enabled=True, expires_at=now[0])
    assert not data.preview("internal_training", selection())["projects"][0]["eligible"]
    store.set_access("tenant-one", enabled=True)
    now[0] += 31 * 86400
    assert not records(unpack(data)[0], "trajectories.jsonl")
    chat(training)
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    assert not records(unpack(data)[0], "trajectories.jsonl")
    assert data.preview("internal_training", selection())["projects"][0]["reason"] == "research_deleted"


def test_sensitive_content_private_thoughts_and_malformed_records_quarantine(training):
    data, _, _ = training
    grant(data)
    for text in (
        "Contact alice@example.org", "Use sk-proj-abcdefghijk123456",
        "Find /home/alice/private/report.csv", "Cookie: session=private",
        "<think>hidden internal deliberation</think>", "Phone +1 (415) 555-0123",
        "Invitation argus_trial_0123456789abcdef",
    ):
        chat(training, text=text)
    with data.analytics._db() as db:
        db.execute("UPDATE journey_events SET record='[]' WHERE sequence=(SELECT min(sequence) FROM journey_events)")
    files, _ = unpack(data)
    serialized = b"\n".join(files.values())
    for secret in (b"alice@example", b"sk-proj-", b"/home/alice", b"session=private",
                   b"hidden internal", b"555-0123", b"argus_trial_"):
        assert secret not in serialized
    report = json.loads(files["quality_report.json"])
    assert report["counts"]["sft"] == 0
    assert report["reason_counts"]["malformed_or_oversized_event"] == 1
    assert report["counts"]["quarantined"] >= 7


def test_no_invented_tools_unmatched_ids_or_failed_ideal_answers(training):
    data, _, now = training
    grant(data)
    chat(training, task="task-real")
    with data.analytics._db() as db:
        for kind, payload in (
            ("tool_call", {"tool_name": "lookup", "call_id": "call-one"}),
            ("tool_result", {"tool_name": "lookup", "call_id": "call-other"}),
            ("tool_result", {"tool_name": "lookup", "call_id": "call-one"}),
        ):
            data.journal._insert(
                db, "tenant-one", "s-project", "runtime_event",
                {"name": "events.jsonl", "offset": len(kind), "call_id": payload["call_id"]}, "task.progress",
                {"kind": kind, **payload}, source_timestamp=now[0],
            )
    preview = data.preview("internal_training", selection())
    assert preview["reason_counts"]["tool_schema_context_unavailable"] == 3
    assert preview["counts"]["tool_sft"] == 0
    now[0] += 1
    with data.analytics._db() as db:
        data.journal._insert(
            db, "tenant-one", "s-project", "runtime_event", {"name": "events.jsonl", "offset": 100},
            "life.mission.failed", {"task_id": "task-real", "status": "failed"}, source_timestamp=now[0],
        )
    preview = data.preview("internal_training", selection())
    assert preview["reason_counts"]["failed_or_corrected_trajectory"] == 1
    files, _ = unpack(data)
    assert not records(files, "sft_train.jsonl")
    assert records(files, "trajectories.jsonl")


def test_grouping_deterministic_shared_task_and_project_isolation(training):
    data, _, _ = training
    for tenant in ("tenant-one", "tenant-two"):
        grant(data, tenant)
        chat(training, tenant=tenant, text="The same task", reply=f"Answer from {tenant}")
    selected = selection() + selection("tenant-two")
    first = data.preview("internal_training", selected)
    second = data.preview("internal_training", list(reversed(selected)))
    assert first["candidates"] == second["candidates"]
    assert len({c["split_group"] for c in first["candidates"]}) == 1
    assert all(c["split"] == "train" for c in first["candidates"])
    grant(data, "tenant-two", internal=False)
    files, _ = unpack(data, projects=selected)
    assert all(row["tenant_id"] == "tenant-one" for row in records(files, "trajectories.jsonl"))
    with pytest.raises(ValueError):
        data.export("internal_training", [{"tenant_id": "tenant-one", "sid": "../../escape"}],
                    review={"content_approved": True})


def test_routes_session_tenant_admin_csrf_and_zip(training):
    data, _, _ = training
    app = FastAPI()
    identities = {
        "tester": {"role": "trial", "tenant": "tenant-one", "readonly": False},
        "other": {"role": "trial", "tenant": "tenant-two", "readonly": False},
        "operator": {"role": "admin", "tenant": "admin", "readonly": False},
        "readonly": {"role": "admin", "tenant": "admin", "readonly": True},
    }
    register_training_routes(
        app, data.analytics, lambda request: identities.get(request.headers.get("x-test-role")),
        journal=data.journal, controls=data.controls,
    )
    with TestClient(app) as client:
        assert client.get("/trial/data-permissions").status_code == 401
        assert client.get("/admin/api/training/preview", headers={"x-test-role": "tester"}).status_code == 403
        assert client.get("/trial/data-permissions", headers={"x-test-role": "operator"}).status_code == 403
        body = {"notice_version": NOTICE_VERSION, "internal_training": True, "external_sharing": False}
        headers = {"x-test-role": "tester", "origin": "http://testserver"}
        assert client.put("/trial/data-permissions", headers=headers, json={**body, "tenant_id": "tenant-two"}).status_code == 400
        assert client.put("/trial/data-permissions", headers=headers, json=body).status_code == 200
        assert client.get("/trial/data-permissions", headers={"x-test-role": "other"}).json()["internal_training"] is False
        chat(training)
        admin = {"x-test-role": "operator", "origin": "http://testserver"}
        preview = client.get("/admin/api/training/preview", headers=admin).json()
        ids = [c["event_id"] for c in preview["candidates"]]
        payload = {"purpose": "internal_training", "projects": selection(),
                   "review": {"content_approved": True, "approved_event_ids": ids}}
        assert client.post("/admin/api/training/export", headers={**admin, "origin": "https://evil.example"}, json=payload).status_code == 403
        assert client.post("/admin/api/training/export", headers={**admin, "x-test-role": "readonly"}, json=payload).status_code == 403
        response = client.post("/admin/api/training/export", headers=admin, json=payload)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert zipfile.is_zipfile(io.BytesIO(response.content))
        assert client.post("/admin/api/training/export", headers=admin, content="[").status_code == 400
        assert any(event["outcome"] == "denied" for event in data.controls.audit_log()["events"])


def test_bounds_stale_approval_and_revocation_during_packaging(training, monkeypatch):
    data, _, _ = training
    grant(data)
    chat(training)
    monkeypatch.setattr(module, "MAX_EXPORT_BYTES", 10)
    with pytest.raises(AnalyticsError, match="size_limit"):
        unpack(data)
    monkeypatch.setattr(module, "MAX_EXPORT_BYTES", 32 * 1024 * 1024)
    with pytest.raises(AnalyticsError, match="no_longer_eligible"):
        unpack(data, review={"content_approved": True, "approved_event_ids": ["a" * 64]})
    package = data._package

    def revoke(*args):
        content = package(*args)
        grant(data, internal=False)
        return content

    monkeypatch.setattr(data, "_package", revoke)
    with pytest.raises(AnalyticsError, match="permissions_changed"):
        unpack(data)


def test_pair_associations_truncation_gaps_and_old_source_not_reconstructed(training):
    data, _, now = training
    grant(data)
    first = chat(training)
    second = chat(training, text="Independent next question")
    with data.analytics._db() as db:
        rows = db.execute("SELECT id,record FROM journey_events").fetchall()
        for row in rows:
            event = json.loads(row["record"])
            if event["kind"] == "http.response" and event["source"]["interaction_id"] == first.id:
                event["source"]["interaction_id"] = second.id
                db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(event), row["id"]))
        data.journal._gap(db, "tenant-one", "s-project", "source_rotated", {"name": "events.jsonl"})
        data.journal._insert(
            db, "tenant-one", "s-project", "runtime_event", {"name": "events.jsonl", "offset": 1},
            "ui.operator", {"text": "Old source must not be imported"}, source_timestamp=now[0] - 100,
        )
    preview = data.preview("internal_training", selection())
    assert not preview["candidates"]
    assert preview["reason_counts"]["unmatched_http_turn"] == 2
    assert preview["reason_counts"]["journal_recording_gap"] == 1
    files, _ = unpack(data)
    assert b"Old source must not be imported" not in b"\n".join(files.values())
    chat(training, text="A truncated response")
    with data.analytics._db() as db:
        row = db.execute("SELECT id,record FROM journey_events ORDER BY sequence DESC LIMIT 1").fetchone()
        event = json.loads(row["record"])
        event["warnings"] = ["http_capture_truncated"]
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(event), row["id"]))
    preview = data.preview("internal_training", selection())
    assert not preview["candidates"]
    assert preview["reason_counts"]["source_warnings_or_truncation"] == 1


def test_overlapping_chat_turns_and_sensitive_failed_context_cannot_be_ideal(training):
    data, _, now = training
    grant(data)
    first = chat(training, task="task-real")
    chat(training, text="Concurrent request")
    with data.analytics._db() as db:
        row = db.execute("SELECT id,record FROM journey_events WHERE record LIKE ?",
                         (f'%"interaction_id":{first.id},"phase":"response"%',)).fetchone()
        event = json.loads(row["record"])
        event["source_timestamp"] = now[0]
        event["ingested_at"] = now[0]
        db.execute("UPDATE journey_events SET record=?,ingested_at=? WHERE id=?",
                   (json.dumps(event), now[0], row["id"]))
    preview = data.preview("internal_training", selection())
    assert preview["reason_counts"]["overlapping_turns"] == 1
    # A failed context must still veto SFT even when its text is quarantined.
    now[0] += 1
    with data.analytics._db() as db:
        data.journal._insert(
            db, "tenant-one", "s-project", "runtime_event", {"name": "events.jsonl", "offset": 10},
            "life.mission.failed", {"status": "failed", "text": "Contact alice@example.org"},
            source_timestamp=now[0],
        )
    preview = data.preview("internal_training", selection())
    assert not preview["candidates"]
    assert preview["reason_counts"]["failed_or_corrected_trajectory"] == 1


def test_runtime_activities_are_not_reasoning_targets_and_incomplete_steps_quarantine(training):
    data, _, _ = training
    empty = data.preview("internal_training")
    assert empty["counts"]["sft"] == empty["counts"]["tool_sft"] == 0
    assert empty["reason_counts"] == {"no_retained_projects": 1}
    assert empty["agentic_tool_training_ready"] is False
    grant(data)
    chat(training, text="Hello.", reply="Hello!")
    # Model the retained-journal contract; capture wiring belongs to research.
    with data.analytics._db() as db:
        row = db.execute("SELECT id,record FROM journey_events ORDER BY sequence DESC LIMIT 1").fetchone()
        event = json.loads(row["record"])
        event["payload"]["result"].update(
            steps=[
                {"kind": "activity", "label": "Request received", "status": "completed"},
                {"kind": "activity", "label": "Manager preparation", "status": "completed"},
            ],
            steps_incomplete=False,
        )
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(event), row["id"]))
    preview = data.preview("internal_training", selection())
    assert len(preview["candidates"]) == 1
    files, _ = unpack(data, review={"content_approved": True, "approved_event_ids": [row["id"]]})
    assert records(files, "sft_train.jsonl") == [
        {"messages": [{"role": "user", "content": "Hello."}, {"role": "assistant", "content": "Hello!"}]},
    ]
    assert b"Request received" in files["trajectories.jsonl"]
    assert b"Manager preparation" not in files["sft_train.jsonl"]
    with data.analytics._db() as db:
        event["payload"]["result"]["steps_incomplete"] = True
        event["payload"]["result"]["steps"].append({
            "kind": "tool_use", "call_id": "real-call", "status": "unconfirmed",
        })
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(event), row["id"]))
    files, _ = unpack(data)
    report = json.loads(files["quality_report.json"])
    assert report["counts"]["sft"] == report["counts"]["tool_sft"] == 0
    assert report["reason_counts"]["runtime_steps_incomplete"] == 1
    assert report["dataset_status"] == "no_eligible_sft_samples"
    assert report["agentic_tool_training_ready"] is False
    assert files["sft_train.jsonl"] == b""


def pi_observations(now):
    """Synthetic genuine Pi event shapes, not fabricated production records."""
    tools = [{
        "name": "sum_numbers", "description": "Add two integers.",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                       "required": ["a", "b"], "additionalProperties": False},
    }]
    user = {"role": "user", "content": [{"type": "text", "text": "Add two and three."}],
            "timestamp": now * 1000}
    call = {"role": "assistant", "content": [
        {"type": "toolCall", "id": "call-real", "name": "sum_numbers", "arguments": {"a": 2, "b": 3}},
    ], "stopReason": "toolUse", "timestamp": now * 1000}
    result = {"role": "toolResult", "toolCallId": "call-real", "toolName": "sum_numbers",
              "content": [{"type": "text", "text": "5"}], "isError": False, "timestamp": now * 1000}
    final = {"role": "assistant", "content": [{"type": "text", "text": "The sum is 5."}],
             "stopReason": "stop", "timestamp": now * 1000}
    receipt = {"toolCallId": "call-real", "toolName": "sum_numbers", "input": {"a": 2, "b": 3}}
    return [
        ("context", {"messages": [user], "tools": tools}),
        ("tool_call", receipt),
        ("tool_result", {**receipt, "content": result["content"], "isError": False, "output_complete": True}),
        ("context", {"messages": [user, call, result], "tools": tools}),
        ("agent_end", {"messages": [user, call, result, final]}),
        ("settled", {}),
    ]


def forward_episode(training, observations, session_id="pi-session-one"):
    data, _, _ = training
    data.journal.poll("tenant-one")
    key = data.capture.begin(
        "tenant-one", "s-project", session_id, observer_verified=True, allowed_tools=["sum_numbers"],
    )["episode_id"]
    for kind, payload in observations:
        result = data.capture.event("tenant-one", "s-project", key, kind, payload)
        if result["state"] == "quarantined":
            break
    return result


def test_forward_pi_tool_episode_exact_schemas_receipts_review_and_flavors(training):
    data, _, now = training
    grant(data)
    now[0] += 1
    outcome = forward_episode(training, pi_observations(now[0]))
    assert outcome["state"] == "complete", outcome
    preview = data.preview("internal_training", selection())
    assert preview["counts"]["tool_candidates"] == 1
    assert preview["counts"]["tool_sft"] == preview["counts"]["sft"] == 0
    event_id = preview["candidates"][0]["event_id"]
    review = {"content_approved": True, "approved_event_ids": [event_id]}
    with pytest.raises(AnalyticsError, match="tool_context_review_required"):
        unpack(data, review=review)
    with pytest.raises(ValueError, match="Invalid operator review"):
        unpack(data, review={**review, "tool_context_approved": "true"})
    files, _ = unpack(data, review={**review, "tool_context_approved": True})
    hf = records(files, "hf_trl_train.jsonl")[0]
    portable = records(files, "sft_train.jsonl")[0]
    assert hf["tools"] == [{"type": "function", "function": pi_observations(now[0])[0][1]["tools"][0]}]
    assert hf["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"a": 2, "b": 3}
    assert json.loads(portable["messages"][1]["tool_calls"][0]["function"]["arguments"]) == {"a": 2, "b": 3}
    assert portable["messages"][2] == {
        "role": "tool", "tool_call_id": "call-real", "content": "5",
    }
    assert hf["messages"][2]["name"] == "sum_numbers"
    assert portable["messages"][-1]["content"] == "The sum is 5."
    report = json.loads(files["quality_report.json"])
    assert report["counts"]["tool_sft"] == 1
    assert report["agentic_tool_training_ready"] is True
    assert report["global_completeness"]["complete"] is False
    canonical = records(files, "trajectories.jsonl")[0]
    assert [event["kind"] for event in canonical["events"]] == [
        "context", "tool_call", "tool_result", "context", "agent_end",
    ]
    assert records(files, "samples.jsonl")[0]["event_ids"] == [e["id"] for e in canonical["events"]]


def test_forward_capture_new_notice_runtime_optin_tenant_revocation_and_delete(training):
    data, _, now = training
    grant(data)
    data.journal.poll("tenant-one")
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET notice_version='training-data-v1'")
    assert not data.capture.authorize("tenant-one", "s-project")["enabled"]
    with pytest.raises(AnalyticsError, match="consent_required"):
        data.capture.begin("tenant-one", "s-project", "pi-old", observer_verified=True, allowed_tools=["sum_numbers"])
    grant(data)
    with pytest.raises(AnalyticsError, match="observer_not_verified"):
        data.capture.begin("tenant-one", "s-project", "pi-new")
    with pytest.raises(AnalyticsError, match="allowlist_required"):
        data.capture.begin("tenant-one", "s-project", "pi-new", observer_verified=True, allowed_tools=["read"])
    key = data.capture.begin(
        "tenant-one", "s-project", "pi-new", observer_verified=True, allowed_tools=["sum_numbers"],
    )["episode_id"]
    with pytest.raises(AnalyticsError, match="not_found"):
        data.capture.event("tenant-two", "s-project", key, "settled", {})
    grant(data, internal=False)
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_tool_episodes").fetchone()[0] == 0
    grant(data)
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_tool_episodes").fetchone()[0] == 0
    assert not data.capture.authorize("tenant-one", "s-project")["enabled"]


def test_forward_capture_missing_schemas_calls_truncation_and_context_fail_closed(training):
    data, _, now = training
    grant(data)
    now[0] += 1
    missing_result = [row for row in pi_observations(now[0]) if row[0] != "tool_result"]
    assert forward_episode(training, missing_result)["reason"] == "invalid_capture_order"
    mismatch = pi_observations(now[0])
    mismatch[2][1]["input"] = {"a": 2, "b": 999}
    assert forward_episode(training, mismatch, "pi-mismatch")["reason"] == "unmatched_tool_call_result"
    no_schema = pi_observations(now[0])
    no_schema[0][1]["tools"] = []
    assert forward_episode(training, no_schema, "pi-no-schema")["reason"] == "invalid_tool_schema"
    truncated = pi_observations(now[0])
    truncated[2][1]["output_complete"] = False
    assert forward_episode(training, truncated, "pi-truncated")["reason"] == "tool_result_excerpt_truncated"
    old_context = pi_observations(now[0])
    old_context[0][1]["messages"][0]["timestamp"] -= 100000
    assert forward_episode(training, old_context, "pi-old-context")["reason"] == "fresh_public_context_required"
    files, _ = unpack(data)
    assert not files["sft_train.jsonl"]
    assert not files["trajectories.jsonl"]
    assert json.loads(files["quality_report.json"])["counts"]["quarantined"] == 5


def test_forward_capture_sensitive_outputs_and_retention(training):
    data, _, now = training
    grant(data)
    now[0] += 1
    private = pi_observations(now[0])
    private[2][1]["content"] = [{"type": "text", "text": "Customer alice@example.org password=secret123"}]
    outcome = forward_episode(training, private)
    assert outcome["state"] == "quarantined"
    with data.analytics._db() as db:
        assert db.execute("SELECT record FROM training_tool_episodes").fetchone()[0] == "[]"
    secret = pi_observations(now[0])
    secret[0][1]["messages"][0]["content"] = [{"type": "text", "text": "sk-proj-abcdefghijk123456"}]
    assert forward_episode(training, secret, "pi-secret")["reason"] == "sensitive_capture_content"
    now[0] += 31 * 86400
    assert data.capture.prune() == 2
    assert not data.preview("internal_training", selection())["counts"]["tool_candidates"]


def test_pi_extension_uses_real_api_shapes_without_private_content_or_runtime_mutation():
    # Execute the actual extension factory without a model, provider, or network.
    script = PI_EXTENSION_SOURCE + r"""
const handlers = new Map(), receipts = [];
const pi = {
  on: (name, handler) => handlers.set(name, handler),
  getActiveTools: () => ["sum_numbers"],
  getAllTools: () => [{name: "sum_numbers", description: "Add two integers.",
                       parameters: {type: "object"}, sourceInfo: {path: "/private/ignored"}}]
};
trainingExtension(async (action, value) => {
  receipts.push({action, value});
  return action === "authorize" ? {enabled: true}
    : action === "begin" ? {episode_id: 1, allowed_tools: ["sum_numbers"]} : {state: "capturing"};
})(pi);
const ctx = {sessionManager: {getSessionId: () => "actual-pi-session"}};
await handlers.get("agent_start")({}, ctx);
const result = await handlers.get("context")({messages: [
  {role: "user", content: "Add two and three.", timestamp: 2000000001000}
]});
await handlers.get("tool_call")({toolCallId: "call-real", toolName: "sum_numbers", input: {a: 2, b: 3}});
await handlers.get("tool_result")({toolCallId: "call-real", toolName: "sum_numbers",
  input: {a: 2, b: 3}, content: [{type: "text", text: "5"}], isError: false});
await handlers.get("agent_end")({messages: [{role: "assistant", timestamp: 2000000001000,
  stopReason: "stop", content: [{type: "thinking", thinking: "NEVER_TRANSMIT_PRIVATE"}]}]});
console.log(JSON.stringify({receipts, mutated: result !== undefined}));
"""
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True,
                            capture_output=True, check=True, timeout=20)
    assert "NEVER_TRANSMIT_PRIVATE" not in result.stdout
    assert "/private/ignored" not in result.stdout
    observed = json.loads(result.stdout)
    assert observed["mutated"] is False
    payloads = [r["value"]["payload"] for r in observed["receipts"] if r["action"] == "event"]
    assert payloads[0]["tools"][0]["parameters"] == {"type": "object"}
    assert payloads[1]["input"] == {"a": 2, "b": 3}
    assert payloads[2]["output_complete"] is True
    assert payloads[-1]["reason"] == "private_or_nontext_context"


def test_combined_onboarding_enables_at_acceptance_without_passive_or_retroactive_grants(training):
    data, _, now = training
    policy = data.permissions("tenant-one")
    assert policy["consent_mode"] == "combined_onboarding"
    assert policy["defaults_after_acceptance"] == {"internal_training": True, "external_sharing": False}
    assert policy["optional_purposes"] == ["external_sharing"]
    assert policy["optional"] is False
    assert not policy["internal_training"] and not policy["external_sharing"]
    assert policy["onboarding"] is None
    with pytest.raises(AnalyticsError, match="combined_notice_version_mismatch"):
        data.accept_onboarding("tenant-one", data.analytics.notice_version, accepted=True)
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    with pytest.raises(AnalyticsError, match="research_consent_required"):
        data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True)
    with pytest.raises(AnalyticsError, match="consent_required"):
        grant(data)
    data.analytics.record_consent("tenant-one", COMBINED_NOTICE_VERSION)
    chat(training, text="Before combined training acceptance")
    now[0] += 1
    accepted_at = now[0]
    result = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True,
    )
    assert result["internal_training"] is True and result["external_sharing"] is True
    assert result["notice_version"] == NOTICE_VERSION
    assert result["granted_at"] == {"internal_training": accepted_at, "external_sharing": accepted_at}
    assert result["onboarding"]["accepted_at"] == accepted_at
    assert not data.preview("internal_training", selection())["candidates"]
    chat(training, text="After combined acceptance")
    assert len(data.preview("external_sharing", selection())["candidates"]) == 1
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_permission_history").fetchone()[0] == 2


def test_combined_onboarding_preserves_revocation_until_explicit_reauthorization(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    data.analytics.record_consent("tenant-one", COMBINED_NOTICE_VERSION)
    for accepted in (False, 1, "true"):
        with pytest.raises(AnalyticsError, match="affirmative_combined_acceptance_required"):
            data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=accepted)
    first = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True,
    )
    original = first["granted_at"]["external_sharing"]
    now[0] += 5
    grant(data, internal=False, external=True)
    now[0] += 5
    ordinary = data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True)
    assert ordinary["internal_training"] is False and ordinary["external_sharing"] is True
    assert ordinary["granted_at"]["external_sharing"] == original
    assert ordinary["onboarding"]["last_accepted_at"] == now[0]
    assert ordinary["onboarding"]["reauthorized_at"] is None
    # A declined older-version purpose is not silently converted into a grant.
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET notice_version='training-data-v1'")
    preserved = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True,
    )
    assert preserved["internal_training"] is False
    now[0] += 5
    renewed = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True, reauthorize=True,
    )
    assert renewed["internal_training"] is True and renewed["external_sharing"] is True
    assert renewed["granted_at"] == {"internal_training": now[0], "external_sharing": now[0]}
    assert renewed["onboarding"]["reauthorized_at"] == now[0]


def test_atomic_notice_recording_cannot_bypass_acceptance_version_or_external_optin(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    with pytest.raises(AnalyticsError, match="affirmative_combined_acceptance_required"):
        data.accept_onboarding(
            "tenant-one", COMBINED_NOTICE_VERSION, accepted=False, record_research=True,
            external_sharing=True,
        )
    with pytest.raises(AnalyticsError, match="combined_notice_version_mismatch"):
        data.accept_onboarding("tenant-one", "operator-analytics-v2", accepted=True, record_research=True)
    assert not data.analytics.consented("tenant-one", COMBINED_NOTICE_VERSION)
    assert data.permissions("tenant-one")["internal_training"] is False
    assert data.permissions("tenant-one")["external_sharing"] is False
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_onboarding_acceptances").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM training_permission_history").fetchone()[0] == 0
    # Trusted-hook simulation of an actually validated notice POST, not redemption.
    result = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
    )
    assert result["internal_training"] is True
    assert result["external_sharing"] is False
    assert result["granted_at"]["internal_training"] == now[0]
    assert result["onboarding"]["accepted_at"] == now[0]
    now[0] += 1
    grant(data, internal=False, external=False)
    result = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
    )
    assert result["internal_training"] is False and result["external_sharing"] is False


def test_offline_team_authority_is_truthful_internal_only_and_forward_scoped(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    policy = {
        "mode": "internal_team_offline", "tenant_ids": ["tenant-one", "tenant-two"],
        "evidence_note": "Owner declaration: all configured testers are our internal team and already authorized offline for internal use only.",
    }
    assert not data.analytics.consented("tenant-one", COMBINED_NOTICE_VERSION)
    assert data.permissions("tenant-one")["requires_affirmative_acceptance"] is True
    result = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert result["internal_training"] is True and result["external_sharing"] is False
    assert result["consent_mode"] == "operator_attested_offline"
    assert result["requires_affirmative_acceptance"] is False
    assert result["authorization"]["source"] == "operator_attested_offline"
    assert result["authorization"]["recorded_at"] == now[0]
    assert result["authorization"]["effective_at"] is None
    assert result["onboarding"] is None
    assert data.analytics.consented("tenant-one", COMBINED_NOTICE_VERSION)
    chat(training, text="An internal team question")
    preview = data.preview("internal_training", selection())
    assert len(preview["candidates"]) == 1
    files, _ = unpack(data, review={
        "content_approved": True, "approved_event_ids": [preview["candidates"][0]["event_id"]],
    })
    assert records(files, "provenance.jsonl")[0]["authorization"]["source"] == "operator_attested_offline"
    assert json.loads(files["manifest.json"])["authorizations"][0]["effective_at"] is None
    assert data.preview("external_sharing", selection())["projects"][0]["reason"] == "offline_policy_internal_only"
    with pytest.raises(AnalyticsError, match="offline_policy_internal_only"):
        grant(data, external=True)
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_onboarding_acceptances").fetchone()[0] == 0
    assert any(event["action"] == "training.offline_authorization" and event["actor"] == "operator"
               for event in data.controls.audit_log()["events"])


def test_offline_policy_accepts_legacy_extra_columns_without_rewriting_receipts(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    with data.analytics._db() as db:
        db.execute("ALTER TABLE training_offline_authorizations ADD COLUMN external_sharing INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE training_offline_authorizations ADD COLUMN sharing_recorded_at REAL")
        db.execute("INSERT INTO training_offline_authorizations VALUES (?,?,?,?,?,?,?,?,?)", (
            "tenant-two", COMBINED_NOTICE_VERSION, NOTICE_VERSION, "operator_attested_offline",
            now[0] - 10, None, "Existing owner declaration.", 1, now[0] - 5,
        ))
        original = tuple(db.execute("SELECT * FROM training_offline_authorizations").fetchone())
    policy = {"mode": "internal_team_offline", "tenant_ids": ["tenant-one"],
              "evidence_note": "Owner explicitly authorized internal-team training."}
    first = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert first["internal_training"] and not first["external_sharing"]
    assert first["authorization"]["effective_at"] is None
    assert first["onboarding"] is None
    now[0] += 10
    repeated = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert repeated["authorization"] == first["authorization"]
    assert repeated["granted_at"] == first["granted_at"]
    with data.analytics._db() as db:
        created = db.execute("SELECT external_sharing,sharing_recorded_at FROM training_offline_authorizations "
                             "WHERE tenant_id='tenant-one'").fetchone()
        assert tuple(created) == (0, None)
        retained = db.execute("SELECT * FROM training_offline_authorizations WHERE tenant_id='tenant-two'").fetchone()
        assert tuple(retained) == original


def test_offline_policy_is_private_preserves_revocations_and_never_resurrects_deletion(training):
    data, _, now = training
    policy = {"mode": "internal_team_offline", "tenant_ids": ["tenant-one"],
              "evidence_note": "Owner confirms internal-team offline authorization, internal use only."}
    with pytest.raises(AnalyticsError, match="outside_configured_internal_team"):
        data.apply_offline_team_authorization("tenant-two", team_policy=policy)
    data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    stamp = data.permissions("tenant-one")["granted_at"]["internal_training"]
    now[0] += 1
    data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert data.permissions("tenant-one")["granted_at"]["internal_training"] == stamp
    grant(data, internal=False, external=False)
    now[0] += 1
    result = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert result["internal_training"] is False
    # Policy is not inferred from receipts on an ordinary/generic instance.
    generic = TrainingData(data.analytics, data.journal, data.controls)
    assert generic.permissions("tenant-one")["requires_affirmative_acceptance"] is True
    assert generic.permissions("tenant-one")["authorization_active"] is False
    grant(data)
    chat(training)
    assert generic.preview("internal_training", selection())["projects"][0]["reason"] == "offline_team_policy_not_active"
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert data.preview("internal_training", selection())["projects"][0]["reason"] == "research_deleted"
    with pytest.raises(ValueError):
        data.set_permissions("tenant-one", {
            "notice_version": NOTICE_VERSION, "internal_training": True, "external_sharing": False,
            "team_policy": policy,
        })




def _authorization_state(data):
    with data.analytics._db() as db:
        return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("training_permissions", "training_permission_history",
                              "training_tool_episodes", "training_sample_reviews")}


def test_offline_policy_preserves_existing_grants_episodes_and_reviews(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True, record_research=True,
    )
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    candidate = data.preview("internal_training", selection())["candidates"][0]
    unpack(data, review={"content_approved": True, "tool_context_approved": True,
                         "approved_event_ids": [candidate["event_id"]], "reviewer_kind": "human_operator"})
    original = _authorization_state(data)
    assert original["training_tool_episodes"] and original["training_sample_reviews"]
    policy = {"mode": "internal_team_offline", "tenant_ids": ["tenant-one"],
              "evidence_note": "Owner explicitly authorized internal-team training."}
    now[0] += 10
    permission = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert _authorization_state(data) == original
    recorded_at = permission["authorization"]["recorded_at"]
    assert recorded_at == now[0] and permission["authorization"]["effective_at"] is None
    assert permission["internal_training"] and not permission["external_sharing"]
    now[0] += 10
    repeated = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert repeated["authorization"]["recorded_at"] == recorded_at
    assert _authorization_state(data) == original


@pytest.mark.parametrize("internal", [False, True])
def test_offline_policy_preserves_older_notice_grants_and_revocations(training, internal):
    data, _, now = training
    grant(data, internal=internal)
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET notice_version='retired'")
    original = _authorization_state(data)
    now[0] += 10
    policy = {"mode": "internal_team_offline", "tenant_ids": ["tenant-one"],
              "evidence_note": "Owner explicitly authorized internal-team training."}
    permission = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    assert not permission["internal_training"]
    assert _authorization_state(data) == original


def test_offline_browser_acceptance_preserves_grant_data_and_explicit_revocation(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    policy = {"mode": "internal_team_offline", "tenant_ids": ["tenant-one"],
              "evidence_note": "Owner explicitly authorized internal-team training."}
    original_permission = data.apply_offline_team_authorization("tenant-one", team_policy=policy)
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    candidate = data.preview("internal_training", selection())["candidates"][0]
    unpack(data, review={"content_approved": True, "tool_context_approved": True,
                         "approved_event_ids": [candidate["event_id"]], "reviewer_kind": "human_operator"})
    original = _authorization_state(data)
    for _ in range(2):
        now[0] += 10
        permission = data.accept_onboarding(
            "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
        )
        assert permission["granted_at"] == original_permission["granted_at"]
        assert permission["authorization"] == original_permission["authorization"]
        assert permission["onboarding"]["last_accepted_at"] == now[0]
        assert _authorization_state(data) == original
        assert data.preview("internal_training", selection())["counts"]["tool_sft"] == 1
    grant(data, internal=False)
    revoked = _authorization_state(data)
    assert not revoked["training_tool_episodes"] and not revoked["training_sample_reviews"]
    now[0] += 10
    assert not data.apply_offline_team_authorization("tenant-one", team_policy=policy)["internal_training"]
    assert not data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
    )["internal_training"]
    assert _authorization_state(data) == revoked


def test_combined_acceptance_external_choice_is_separate_and_strict(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    first = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
    )
    assert first["internal_training"] and not first["external_sharing"]
    grant(data, internal=False)
    now[0] += 5
    external = data.accept_onboarding(
        "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True,
    )
    assert not external["internal_training"] and external["external_sharing"]
    assert external["granted_at"]["external_sharing"] == now[0]
    later = data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True)
    assert not later["internal_training"] and later["external_sharing"]
    for invalid in (1, "true", None):
        with pytest.raises(AnalyticsError, match="affirmative_combined_acceptance_required"):
            data.accept_onboarding(
                "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=invalid,
            )
    # A broader new notice cannot silently inherit an older external grant.
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET notice_version='training-data-v2'")
    upgraded = data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True)
    assert not upgraded["internal_training"] and not upgraded["external_sharing"]


def test_combined_research_and_training_acceptance_roll_back_together(training):
    import sqlite3

    data, _, _ = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    with data.analytics._db() as db:
        db.execute("""CREATE TRIGGER fail_synthetic_grant BEFORE INSERT ON training_permissions
                      BEGIN SELECT RAISE(ABORT, 'synthetic grant failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic grant failure"):
        data.accept_onboarding(
            "tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True,
        )
    assert not data.analytics.consented("tenant-one", COMBINED_NOTICE_VERSION)
    assert data.permissions("tenant-one")["onboarding"] is None
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_permissions").fetchone()[0] == 0


def test_adding_external_permission_preserves_existing_internal_tool_samples(training):
    data, _, now = training
    data.analytics.notice_version = COMBINED_NOTICE_VERSION
    data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True, record_research=True)
    data.journal.poll("tenant-one")
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    before = data.preview("internal_training", selection())["candidates"]
    assert len(before) == 1
    now[0] += 1
    data.accept_onboarding("tenant-one", COMBINED_NOTICE_VERSION, accepted=True, external_sharing=True)
    internal = data.preview("internal_training", selection())["candidates"]
    assert [item["event_id"] for item in internal] == [item["event_id"] for item in before]
    # Additional rights apply forward: old internal samples acquire no new external grant.
    assert not data.preview("external_sharing", selection())["candidates"]


def test_preview_pagination_and_filters_reach_authorized_projects_after_first_twenty(training):
    data, _, _ = training
    grant(data, "tenant-two")
    chat(training, tenant="tenant-two")
    with data.analytics._db() as db:
        db.executemany(
            "INSERT INTO journey_projects(tenant_id,sid,notice_version) VALUES (?,?,?)",
            [("tenant-one", f"s-unconsented-{index:02}", data.analytics.notice_version) for index in range(25)],
        )
        # Foreign metadata must not consume the configured tenants' page limit.
        db.execute("INSERT INTO journey_projects(tenant_id,sid,notice_version) VALUES (?,?,?)",
                   ("a-foreign", "s-hidden", data.analytics.notice_version))
    first = data.preview("internal_training")
    assert len(first["projects"]) == 20 and first["total_projects"] == 26
    assert first["has_more_projects"] and first["next_offset"] == 20
    assert not any(project["eligible"] for project in first["projects"])
    second = data.preview("internal_training", offset=first["next_offset"])
    assert len(second["projects"]) == 6 and not second["has_more_projects"]
    assert any(project["eligible"] and project["tenant_id"] == "tenant-two" for project in second["projects"])
    filtered = data.preview("internal_training", tenant="tenant-two", query="project")
    assert filtered["total_projects"] == 1 and len(filtered["candidates"]) == 1
    assert data.preview("internal_training", query="%' OR 1=1 --")["total_projects"] == 0
    assert data.preview("internal_training", offset=100)["projects"] == []
    with pytest.raises(AnalyticsError, match="tenant_not_found"):
        data.preview("internal_training", tenant="a-foreign")
    with pytest.raises(ValueError, match="offset"):
        data.preview("internal_training", offset=-1)


def test_preview_route_passes_browse_filters_and_validates_offsets(training):
    data, _, _ = training
    grant(data, "tenant-two")
    chat(training, tenant="tenant-two")
    app = FastAPI()
    register_training_routes(app, data.analytics, lambda request: {
        "role": "admin", "tenant": "admin", "readonly": True,
    }, journal=data.journal, controls=data.controls)
    with TestClient(app) as client:
        result = client.get("/admin/api/training/preview", params={
            "tenant": "tenant-two", "query": "project", "offset": 0,
        })
        assert result.status_code == 200
        assert result.json()["total_projects"] == 1
        assert result.json()["filters"] == {"tenant": "tenant-two", "query": "project"}
        assert client.get("/admin/api/training/preview", params={"offset": -1}).status_code == 422
        assert client.get("/admin/api/training/preview", params={"offset": 2**63}).status_code == 422
        assert client.get("/admin/api/training/preview", params={"tenant": "absent"}).status_code == 404


@pytest.mark.parametrize("title", [
    "论文生成：Monte Carlo方差缩减", "数学猜想：floor-sum严格验证", "工程优化：加权区间调度",
])
def test_preview_uses_safe_real_project_titles_only_for_eligible_projects(training, monkeypatch, title):
    data, _, _ = training
    for tenant in ("tenant-one", "tenant-two"):
        path = data.analytics.tenants[tenant]["data_dir"] / "home/.argus-skill/projects/s-project/session.json"
        path.write_text(json.dumps({"id": "s-project", "display_name": title}))
        chat(training, tenant=tenant)
    grant(data)
    seen = []
    original = data.analytics.projects

    def metadata(tenant):
        seen.append(tenant)
        return original(tenant)

    monkeypatch.setattr(data.analytics, "projects", metadata)
    result = data.preview("internal_training", selection() + selection("tenant-two"))
    assert result["projects"][0]["title"] == title
    assert result["projects"][1]["title"] == "s-project"
    assert seen == ["tenant-one"]
    path = data.analytics.tenants["tenant-one"]["data_dir"] / "home/.argus-skill/projects/s-project/session.json"
    path.unlink()
    assert data.preview("internal_training", selection())["projects"][0]["title"] == "s-project"


def test_unreadable_project_name_falls_back_without_losing_training_preview(training, monkeypatch):
    data, _, _ = training
    grant(data)
    chat(training)

    def unavailable(tenant):
        raise PermissionError("Synthetic unavailable metadata")

    monkeypatch.setattr(data.analytics, "projects", unavailable)
    result = data.preview("internal_training", selection())
    assert result["projects"][0]["title"] == "s-project"
    assert result["counts"]["candidates"] == 1


def test_automated_acceptance_exports_evidence_without_claiming_human_review(training):
    data, _, now = training
    grant(data)
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    candidate = data.preview("internal_training", selection())["candidates"][0]
    review = {"content_approved": True, "tool_context_approved": True,
              "approved_event_ids": [candidate["event_id"]], "reviewer_kind": "automated_acceptance"}
    with pytest.raises(AnalyticsError, match="automated_acceptance_evidence_required"):
        unpack(data, review=review)
    digest = hashlib.sha256(b"Independent synthetic acceptance report").hexdigest()
    files, _ = unpack(data, review={**review, "evidence_sha256": digest})
    manifest = json.loads(files["manifest.json"])
    assert manifest["reviewer_kind"] == "automated_acceptance"
    assert manifest["human_reviewed"] is False and manifest["evidence_sha256"] == digest
    report = json.loads(files["quality_report.json"])
    assert report["review"]["reviewer_kind"] == "automated_acceptance"
    evidence = records(files, "samples.jsonl")[0]["quality_evidence"]
    assert evidence["reviewer_kind"] == "automated_acceptance"
    assert evidence["human_reviewed"] is False and evidence["evidence_sha256"] == digest
    events = data.review_audit()["events"]
    completed = next(event for event in events if event["outcome"] == "completed")
    assert completed["action"] == "training.export.automated_acceptance"
    assert completed["actor"] == "operator" and completed["evidence_sha256"] == digest
    assert b"not human review" in files["README.txt"]
    with data.analytics._db() as db:
        db.execute("DELETE FROM research_audit")
        assert db.execute("SELECT count(*) FROM training_review_evidence").fetchone()[0] == 0


def test_unspecified_review_stays_unspecified_and_reviewer_fields_are_validated(training):
    data, _, _ = training
    grant(data)
    chat(training)
    event = data.preview("internal_training", selection())["candidates"][0]["event_id"]
    review = {"content_approved": True, "approved_event_ids": [event]}
    files, _ = unpack(data, review=review)
    assert json.loads(files["manifest.json"])["reviewer_kind"] == "unspecified"
    assert records(files, "samples.jsonl")[0]["quality_evidence"]["human_reviewed"] is False
    for invalid in ({"reviewer_kind": "inferred_human"}, {"reviewer_kind": True},
                    {"evidence_sha256": "not-a-digest"}):
        with pytest.raises(ValueError):
            unpack(data, review={**review, **invalid})


@pytest.mark.parametrize("text", [
    "0.123456789", "0.002425301", "-0.123456789", "+1.234567890", "1.23e-10",
    "size,score,seconds\n100,0.123456789,0.002425301\n1000,-5.123456789,0.00512",
    "100 1000 5000\n-100 -1000 -5000", "iterations=13800138000",
])
def test_scientific_metrics_are_not_classified_as_phone_numbers(text):
    from argus_skill.trial.training_capture import _hosted_sensitive

    assert module._SENSITIVE.search(text) is None
    assert not _hosted_sensitive({"content": text})


@pytest.mark.parametrize("text", [
    "+14155550123", "+86 13800138000", "+1 (415) 555-0123", "415-555-0123", "(415) 555-0123",
    "415.555.0123", "415 555 0123", "phone: 13800138000", '"mobile":"13800138000"',
    "手机号：13800138000", "alice@example.org", "password=secret123", "/home/alice/private.txt",
])
def test_specific_phone_and_other_sensitive_indicators_still_quarantine(text):
    from argus_skill.trial.training_capture import _hosted_sensitive

    assert module._SENSITIVE.search(text) is not None
    assert _hosted_sensitive({"content": text})


def test_data_workbench_and_audit_reuse_admin_session_boundary(training):
    from argus_skill.trial.data_page import PAGE, SCRIPT

    data, _, _ = training
    app = FastAPI()
    identities = {
        "tester": {"role": "trial", "tenant": "tenant-one", "readonly": False},
        "admin": {"role": "admin", "tenant": "admin", "readonly": True},
    }
    register_training_routes(app, data.analytics, lambda request: identities.get(request.headers.get("x-role")),
                             journal=data.journal, controls=data.controls)
    with TestClient(app) as client:
        for path in ("/admin/data", "/admin/data/app.js", "/admin/api/training/audit"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"x-role": "tester"}).status_code == 403
            assert client.get(path, headers={"x-role": "admin"}).status_code == 200
        assert client.get("/admin/data", headers={"x-role": "admin"}).text == PAGE
        assert client.get("/admin/data/app.js", headers={"x-role": "admin"}).text == SCRIPT
    grant(data)
    chat(training, text="Customer alice@example.org")
    preview = data.preview("internal_training", selection())
    assert preview["diagnostics"] and not preview["candidates"]
    assert "alice@example.org" not in json.dumps(preview["diagnostics"])
    assert preview["capture_status"]["scope"] == "retained_tool_episodes_for_configured_tenants"


def test_review_receipt_survives_refresh_but_never_a_changed_sample(training):
    data, _, _ = training
    grant(data)
    chat(training, reply="Synthetic reviewed response body")
    candidate = data.preview("internal_training", selection())["candidates"][0]
    unpack(data, review={"content_approved": True, "reviewer_kind": "human_operator",
                         "approved_event_ids": [candidate["event_id"]]})
    fresh = TrainingData(data.analytics, data.journal, data.controls)
    preview = fresh.preview("internal_training", selection())
    assert preview["counts"]["sft"] == 1
    assert "explicit_quality_review_required" not in preview["reason_counts"]
    evidence = preview["candidates"][0]["quality_evidence"]
    assert evidence["persisted"] and evidence["reviewer_kind"] == "human_operator"
    with data.analytics._db() as db:
        receipt = dict(db.execute("SELECT * FROM training_sample_reviews").fetchone())
        assert "Synthetic reviewed response body" not in json.dumps(receipt)
        row = db.execute("SELECT record FROM journey_events WHERE id=?", (candidate["event_id"],)).fetchone()
        record = json.loads(row["record"])
        record["payload"]["result"]["reply"] = "Different response with the same event identifier"
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(record), candidate["event_id"]))
    changed = fresh.preview("internal_training", selection())
    assert changed["counts"]["sft"] == 0
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_sample_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("remove", ["source", "project", "revoke"])
def test_review_receipts_follow_source_deletion_project_deletion_and_revocation(training, remove):
    data, _, now = training
    grant(data)
    chat(training)
    candidate = data.preview("internal_training", selection())["candidates"][0]
    unpack(data, review={"content_approved": True, "approved_event_ids": [candidate["event_id"]]})
    if remove == "source":
        with data.analytics._db() as db:
            db.execute("DELETE FROM journey_events WHERE id=?", (candidate["event_ids"][0],))
    elif remove == "project":
        data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    else:
        grant(data, internal=False)
        now[0] += 1
        grant(data)
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_sample_reviews").fetchone()[0] == 0
    assert data.preview("internal_training", selection())["counts"]["sft"] == 0


def test_tool_review_remains_visible_and_expires_with_its_source_episode(training):
    data, _, now = training
    grant(data)
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    candidate = data.preview("internal_training", selection())["candidates"][0]
    digest = hashlib.sha256(b"Independent acceptance evidence").hexdigest()
    unpack(data, review={"content_approved": True, "tool_context_approved": True,
                        "reviewer_kind": "automated_acceptance", "evidence_sha256": digest,
                        "approved_event_ids": [candidate["event_id"]]})
    refreshed = data.preview("internal_training", selection())
    assert refreshed["counts"]["tool_sft"] == 1 and refreshed["agentic_tool_training_ready"]
    assert "explicit_tool_context_review_required" not in refreshed["reason_counts"]
    evidence = refreshed["candidates"][0]["quality_evidence"]
    assert evidence["reviewer_kind"] == "automated_acceptance" and evidence["evidence_sha256"] == digest
    assert evidence["human_reviewed"] is False
    now[0] += 31 * 86400
    data.capture.prune()
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_sample_reviews").fetchone()[0] == 0
    assert data.preview("internal_training", selection())["counts"]["tool_sft"] == 0
