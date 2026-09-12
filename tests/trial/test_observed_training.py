"""V2 stores actual received public observations independently of SFT quality."""
import io
import json
import zipfile

import pytest
from test_training_data import grant
from test_training_data import training as training

from argus_skill.trial.analytics import AnalyticsError
from argus_skill.trial.training_capture import HOSTED_PROFILE, OBSERVED_POLICY


def begin(training, *, role="planner.cycle0", session="continued-session", tools=(), task="task-real", tenant="tenant-one"):
    data, _, now = training
    grant(data, tenant=tenant)
    now[0] += 1
    data.journal.poll(tenant)
    return data.capture.begin(
        tenant, "s-project", session, observer_verified=True, allowed_tools=list(tools),
        runtime_profile=HOSTED_PROFILE,
        runtime_metadata={"capture_policy": OBSERVED_POLICY, "run_label": role, "mission_id": task},
    )["episode_id"]


def send(data, episode, kind, payload):
    return data.capture.event("tenant-one", "s-project", episode, kind, payload)


def events(data, episode):
    with data.analytics._db() as db:
        return data.capture.events(db, episode)


def test_four_roles_zero_tools_real_application_instructions_and_format_mismatches_are_retained(training):
    data, _, _ = training
    for role in ("manager-stage", "planner.cycle0", "engineer-r1", "reviewer"):
        episode = begin(training, role=role)
        context = {"messages": [{"role": "user", "content": "Review /home/team/project and the public skill."}], "tools": []}
        provider = {"messages": [{"role": "system", "content": "Actual application role instruction."},
                                 {"role": "developer", "content": "Use reproducible evidence."},
                                 {"role": "user", "content": "Actual provider input differs from context."}],
                    "tools": [], "model": "synthetic-observer"}
        assert send(data, episode, "context", context)["state"] == "capturing"
        send(data, episode, "provider_request", provider)
        send(data, episode, "agent_end", {"messages": [{"role": "assistant", "content": "A text-only role result."}],
                                          "private_blocks_excluded": True})
        assert send(data, episode, "settled", {})["state"] == "complete"
        stored = events(data, episode)
        assert stored[0]["payload"] == context and stored[1]["payload"] == provider
        assert [item["kind"] for item in stored] == ["context", "provider_request", "agent_end", "settled"]
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_tool_episodes").fetchone()[0] == 4


def test_failed_unmatched_tools_and_transport_interruption_preserve_received_prefix(training):
    data, _, _ = training
    episode = begin(training, role="engineer-r1", tools=["custom_simulator"])
    payload = {"toolCallId": "actual-call", "toolName": "custom_simulator", "input": {"path": "/home/team/custom"}}
    send(data, episode, "tool_call", payload)
    send(data, episode, "tool_result", {**payload, "content": [{"type": "text", "text": "Execution failed."}],
                                       "isError": True, "output_complete": False})
    send(data, episode, "capture_warning", {"reason": "one_projection_failed", "event_kind": "context"})
    send(data, episode, "context", {"messages": [{"role": "user", "content": "Continue after the warning."}], "tools": []})
    assert send(data, episode, "quarantine", {"reason": "runtime_call_unsettled"})["state"] == "interrupted"
    stored = events(data, episode)
    assert len(stored) == 5 and stored[0]["payload"] == payload
    assert stored[1]["payload"]["isError"] is True
    assert stored[-1]["payload"]["reason"] == "runtime_call_unsettled"


def test_private_structured_blocks_are_excluded_without_erasing_public_io(training):
    data, _, _ = training
    episode = begin(training)
    send(data, episode, "agent_end", {"messages": [{"role": "assistant", "reasoning_content": "PRIVATE_REASONING_SENTINEL",
         "content": [{"type": "thinking", "thinking": "PRIVATE_THINKING_SENTINEL"},
                     {"type": "text", "text": "The word thinking in public prose is retained."}]}]})
    stored = events(data, episode)
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(stored)
    assert "PRIVATE_THINKING_SENTINEL" not in json.dumps(stored)
    assert "thinking in public prose" in json.dumps(stored)
    assert stored[-1]["kind"] == "capture_warning"
    assert stored[-1]["payload"]["reason"] == "private_blocks_excluded"


def test_v2_has_no_legacy_episode_event_or_cumulative_storage_gate(training, monkeypatch):
    from argus_skill.trial import training_capture

    data, _, _ = training
    for name in ("MAX_EPISODES", "HOSTED_OBSERVATIONS", "HOSTED_EPISODE_BYTES", "MAX_CAPTURE_BYTES"):
        monkeypatch.setattr(training_capture, name, 1)
    first, second = begin(training), begin(training)
    for index in range(4):
        send(data, first, "capture_warning", {"reason": "synthetic_observation", "index": index})
    send(data, second, "context", {"messages": [], "tools": []})
    assert len(events(data, first)) == 4 and len(events(data, second)) == 1


def test_oversized_single_event_records_gap_and_next_events_continue(training, monkeypatch):
    from argus_skill.trial import training_capture

    data, _, _ = training
    episode = begin(training)
    send(data, episode, "context", {"messages": [], "tools": []})
    monkeypatch.setattr(training_capture, "HOSTED_PAYLOAD_BYTES", 64)
    assert send(data, episode, "context", {"messages": [{"content": "x" * 1000}], "tools": []})["state"] == "capturing"
    send(data, episode, "context", {"messages": [], "tools": []})
    send(data, episode, "settled", {})
    stored = events(data, episode)
    assert [row["kind"] for row in stored] == ["context", "capture_warning", "context", "settled"]
    assert stored[1]["payload"]["reason"] == "capture_payload_oversized"


def test_revocation_and_project_deletion_remove_v2_observation_rows(training):
    data, _, _ = training
    episode = begin(training)
    send(data, episode, "context", {"messages": [], "tools": []})
    grant(data, internal=False)
    with pytest.raises(AnalyticsError):
        send(data, episode, "settled", {})
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_observed_events").fetchone()[0] == 0
    episode = begin(training)
    send(data, episode, "context", {"messages": [], "tools": []})
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    with data.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_observed_events").fetchone()[0] == 0


def test_raw_observation_pages_keep_four_roles_and_unassigned_runs_visible(training):
    data, _, _ = training
    for role, task in (("manager-stage", None), ("planner.cycle0", None), ("engineer-r1", "task-real"), ("reviewer", "task-real")):
        episode = begin(training, role=role, task=task)
        send(data, episode, "context", {"messages": [{"role": "system", "content": "Actual role input."}], "tools": []})
        send(data, episode, "agent_end", {"messages": [{"role": "assistant", "content": "Actual role output."}]})
        send(data, episode, "settled", {})
    cursor, collected, roles = None, {}, set()
    for _ in range(20):
        page = data.observations("internal_training", "tenant-one", "s-project", cursor=cursor, limit=2)
        for episode in page["episodes"]:
            roles.add(episode["role"])
            for event in episode["events"]:
                key = (episode["episode_id"], event["sequence"])
                assert key not in collected
                collected[key] = event
        if not page["pagination"]["has_more"]:
            break
        assert page["pagination"]["next_cursor"] != cursor
        cursor = page["pagination"]["next_cursor"]
    assert len(collected) == 12 and roles == {"manager", "planner", "engineer", "reviewer"}
    task = data.observations("internal_training", "tenant-one", "s-project", "task-real")
    assert {episode["role"] for episode in task["episodes"]} == {"engineer", "reviewer"}
    assert all(episode["quality"]["state"] == "not_evaluated" for episode in task["episodes"])


def test_raw_export_needs_no_review_and_keeps_unsettled_and_failed_tool_records(training):
    data, _, _ = training
    first = begin(training, role="manager-stage", task=None)
    send(data, first, "context", {"messages": [{"role": "developer", "content": "Actual application instruction."}], "tools": []})
    second = begin(training, tools=["custom_simulator"])
    send(data, second, "tool_call", {"toolCallId": "call-real", "toolName": "custom_simulator", "input": {"x": 1}})
    send(data, second, "quarantine", {"reason": "runtime_call_unsettled"})
    chunks, name = data.export_observations("internal_training", [{"tenant_id": "tenant-one", "sid": "s-project"}])
    assert not isinstance(chunks, bytes) and name.endswith("observations.zip")
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = [json.loads(line) for line in archive.read("observations.jsonl").splitlines()]
    assert manifest["episodes"] == 2 and manifest["observations"] == 3
    assert "sha256" not in json.dumps(manifest)
    headers = [row for row in records if row["kind"] == "episode"]
    assert {row["state"] for row in headers} == {"capturing", "interrupted"}
    assert [row["event"]["kind"] for row in records if row["kind"] == "observation"] == ["context", "tool_call", "quarantine"]
    assert "Actual application instruction." in json.dumps(records)


def test_raw_view_and_export_recheck_purpose_tenant_and_deletion(training):
    data, _, _ = training
    first = begin(training)
    send(data, first, "context", {"messages": [{"role": "user", "content": "Tenant one original."}], "tools": []})
    second = begin(training, tenant="tenant-two")
    data.capture.event("tenant-two", "s-project", second, "context",
                       {"messages": [{"role": "user", "content": "Tenant two original."}], "tools": []})
    one = data.observations("internal_training", "tenant-one", "s-project")
    assert "Tenant two" not in json.dumps(one)
    with pytest.raises(AnalyticsError, match="purpose_consent_required"):
        data.observations("external_sharing", "tenant-one", "s-project")
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    with pytest.raises(AnalyticsError, match="research_deleted"):
        data.export_observations("internal_training", [{"tenant_id": "tenant-one", "sid": "s-project"}])
    assert data.observations("internal_training", "tenant-two", "s-project")["episodes"]


def test_observed_summary_counts_real_pairs_without_promoting_quality(training):
    from argus_skill.trial.collaboration_data import CollaborationData

    data, _, _ = training
    episode = begin(training, tools=["custom_simulator"])
    call = {"toolCallId": "real-call", "toolName": "custom_simulator", "input": {"x": 2}}
    send(data, episode, "tool_call", call)
    send(data, episode, "tool_result", {**call, "content": [{"type": "text", "text": "Real failure result"}], "isError": True})
    send(data, episode, "settled", {})
    result = CollaborationData(data).overview(tenant="tenant-one")
    assert result["counts"]["observed_episodes"] == 1 and result["counts"]["observed_events"] == 3
    assert result["counts"]["tool_pairs"] == 1
    assert result["counts"]["approved_samples"] == result["counts"]["candidates"] == 0
    raw = data.observations("internal_training", "tenant-one", "s-project")
    assert raw["episodes"][0]["tool_pairs"][0]["status"] == "error"
    assert raw["episodes"][0]["quality"]["state"] == "not_evaluated"


def test_legacy_approved_sample_and_raw_source_are_preserved(training):
    from test_training_data import forward_episode, pi_observations

    data, _, now = training
    grant(data)
    now[0] += 1
    assert forward_episode(training, pi_observations(now[0]))["state"] == "complete"
    selection = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    candidate = data.preview("internal_training", selection)["candidates"][0]
    data.export("internal_training", selection, review={"content_approved": True, "tool_context_approved": True,
                "approved_event_ids": [candidate["event_id"]], "reviewer_kind": "automated_acceptance", "evidence_sha256": "a" * 64})
    with data.analytics._db() as db:
        original = db.execute("SELECT record FROM training_tool_episodes").fetchone()[0]
    page = data.observations("internal_training", "tenant-one", "s-project")
    assert page["episodes"][0]["quality"]["approved"] is True
    chunks, _ = data.export_observations("internal_training", selection)
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        rows = [json.loads(line) for line in archive.read("observations.jsonl").splitlines()]
    assert rows[0]["quality"]["state"] == "approved"
    assert [row["event"] for row in rows[1:]] == json.loads(original)
    with data.analytics._db() as db:
        assert db.execute("SELECT record FROM training_tool_episodes").fetchone()[0] == original


def test_export_revocation_after_read_discards_archive_and_abandoned_iterator_cleans_up(training, monkeypatch, tmp_path):
    from argus_skill.trial import training_observations

    data, _, _ = training
    episode = begin(training)
    send(data, episode, "context", {"messages": [], "tools": []})
    monkeypatch.setattr(training_observations.tempfile, "tempdir", str(tmp_path))
    selection = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    chunks, _ = data.export_observations("internal_training", selection)
    assert list(tmp_path.glob("argus-observations-*.zip"))
    chunks.close()
    assert not list(tmp_path.glob("argus-observations-*.zip"))
    original = training_observations.ObservedData._descriptor

    def revoke(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        grant(data, internal=False)
        return result

    monkeypatch.setattr(training_observations.ObservedData, "_descriptor", revoke)
    with pytest.raises(AnalyticsError, match="purpose_consent_required"):
        data.export_observations("internal_training", selection)
    assert not list(tmp_path.glob("argus-observations-*.zip"))
