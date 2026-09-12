"""Recovery fixtures are synthetic; never open a live trial database or log."""
import json
from datetime import datetime, timezone

import pytest
from test_training_data import grant
from test_training_data import training as training

from argus_skill.trial import training_recovery as module
from argus_skill.trial.training_capture import HOSTED_PROFILE, OBSERVED_POLICY

SESSION = "12345678-1234-7234-8234-123456789abc"


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


def legacy(training, *, role="planner.cycle0", session=SESSION, task="task-real"):
    data, _, now = training
    grant(data)
    now[0] += 1
    data.journal.poll("tenant-one")
    episode = data.capture.begin(
        "tenant-one", "s-project", session, observer_verified=True, allowed_tools=["read"],
        runtime_profile=HOSTED_PROFILE,
        runtime_metadata={"run_label": role, "mission_id": task},
    )["episode_id"]
    with data.analytics._db() as db:
        db.execute("UPDATE training_tool_episodes SET state='quarantined',reason='sensitive_capture_content',record='[]' WHERE id=?", (episode,))
    return episode


def native(training, messages=None, *, tenant="tenant-one", session=SESSION, header_id=None):
    data, _, now = training
    start = now[0]
    directory = data.analytics.tenants[tenant]["data_dir"] / "home/.argus-skill/pi-sessions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"synthetic_{session}.jsonl"
    messages = messages if messages is not None else [{"role": "user", "content": "Actual public input."},
                                                    {"role": "assistant", "content": [{"type": "text", "text": "Actual public output."}]}]
    rows = [{"type": "session", "id": header_id or session, "timestamp": iso(start), "cwd": "/shared/workspace"}]
    for index, message in enumerate(messages, 1):
        stamp = message.get("timestamp", (start + index) * 1000) / 1000
        rows.append({"type": "message", "id": f"native-{index}", "parentId": f"native-{index-1}" if index > 1 else None,
                     "timestamp": iso(stamp), "message": {"timestamp": stamp * 1000, **message}})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    now[0] += len(messages) + 10
    return path


def snapshot(data, episode):
    with data.analytics._db() as db:
        row = db.execute("SELECT * FROM training_tool_episodes WHERE id=?", (episode,)).fetchone()
        events = [dict(row) for row in db.execute("SELECT * FROM training_observed_events WHERE episode_id=? ORDER BY sequence", (episode,))]
    return dict(row) if row else None, events


def read(data, episode):
    row, _ = snapshot(data, episode)
    with data.analytics._db() as db:
        events = data.capture.events(db, episode)
    return row, json.loads(row["runtime_metadata"]), events


def test_recovery_preserves_actual_public_messages_timestamps_order_and_identity(training):
    data, _, now = training
    episode = legacy(training)
    original = snapshot(data, episode)[0]
    base = now[0]
    messages = [
        {"role": "user", "content": "Inspect /home/team/public.csv", "timestamp": (base + 3) * 1000},
        {"role": "assistant", "content": [{"type": "toolCall", "id": "call-real", "name": "read", "arguments": {"path": "/home/team/public.csv"}}],
         "timestamp": (base + 1) * 1000},
        {"role": "toolResult", "toolCallId": "call-real", "toolName": "read", "content": [{"type": "text", "text": "Actual contents"}],
         "isError": False, "timestamp": (base + 2) * 1000},
    ]
    path = native(training, messages)
    result = module.recover_episode(data, episode)
    assert result == {"episode_id": episode, "status": "recovered", "state": "interrupted", "event_count": 3}
    row, runtime, events = read(data, episode)
    assert [event["kind"] for event in events] == ["session_message"] * 3
    assert [event["sequence"] for event in events] == [0, 1, 2]
    assert [event["observed_at"] for event in events] == [base + 3, base + 1, base + 2]
    assert [event["payload"]["messages"][0] for event in events] == messages
    assert [event["payload"]["native_entry_id"] for event in events] == ["native-1", "native-2", "native-3"]
    assert row["state"] == "interrupted" and row["record"] == "[]"
    for field in ("id", "tenant_id", "sid", "session_id", "started_at", "grants", "allowed_tools", "runtime_profile"):
        assert row[field] == original[field]
    assert runtime["mission_id"] == "task-real" and runtime["capture_policy"] == OBSERVED_POLICY
    recovery = runtime["recovery"]
    assert recovery["source"] == "pi_session_jsonl"
    assert "source_sha256" not in recovery
    assert recovery["source_file"] == f"home/.argus-skill/pi-sessions/{path.name}"
    assert recovery["source_bytes"] == path.stat().st_size
    assert recovery["original_state"] == "quarantined" and recovery["original_reason"] == "sensitive_capture_content"
    assert recovery["binding"]["session_id"] == SESSION and recovery["binding"]["mission_id"] == "task-real"
    assert recovery["provider_requests_available"] is False and recovery["tool_schemas_available"] is False
    assert recovery["original_episode_context_available"] is False
    assert recovery["includes_resumed_turns_and_branches"] is True
    assert row["updated_at"] == base + 3
    assert recovery["recovered_at"] > row["updated_at"]
    page = data.observations("internal_training", "tenant-one", "s-project")
    descriptor = page["episodes"][0]
    assert descriptor["quality"] == {"state": "not_evaluated", "approved": False}
    assert descriptor["collection"]["event_counts"] == {"session_message": 3}


def test_text_only_manager_and_idempotence_without_invented_events(training):
    data, _, _ = training
    episode = legacy(training, role="manager-stage", task=None)
    native(training)
    assert module.recover_episode(data, episode)["status"] == "recovered"
    before = snapshot(data, episode)
    assert module.recover_episode(data, episode)["status"] == "already_recovered"
    assert snapshot(data, episode) == before
    _, runtime, events = read(data, episode)
    assert runtime["mission_id"] is None and runtime["recovery"]["binding"]["mission_id"] is None
    assert {event["kind"] for event in events} == {"session_message"}
    assert not any("tools" in event["payload"] for event in events)


@pytest.mark.parametrize("role", ["engineer-r1", "reviewer"])
def test_engineer_and_reviewer_legacy_public_messages_recover(training, role):
    data, _, _ = training
    episode = legacy(training, role=role)
    native(training)
    assert module.recover_episode(data, episode)["status"] == "recovered"
    row, runtime, events = read(data, episode)
    assert row["state"] == "interrupted" and runtime["run_label"] == role
    assert runtime["mission_id"] == "task-real"
    assert [event["kind"] for event in events] == ["session_message", "session_message"]
    assert events[0]["payload"]["messages"][0]["content"] == "Actual public input."
    assert events[1]["payload"]["messages"][0]["content"] == [{"type": "text", "text": "Actual public output."}]


def test_structured_private_fields_removed_but_business_arguments_and_prose_remain(training):
    data, _, _ = training
    episode = legacy(training)
    arguments = {"thoughtSignature": "business-value", "reasoning": "business-purpose", "nested": {"signature": "customer-field"}}
    native(training, [
        {"role": "assistant", "thinking": "PRIVATE_FIELD", "thoughtSignature": "PRIVATE_MESSAGE_SIGNATURE", "content": [
            {"type": "thinking", "thinking": "PRIVATE_THINKING", "signature": "PRIVATE_SIG"},
            {"type": "text", "text": "Public prose says thinking, alice@example.org and /home/team/data.", "thoughtSignature": "PRIVATE_TEXT_SIGNATURE"},
            {"type": "toolCall", "id": "real-call", "name": "read", "arguments": arguments,
             "thoughtSignature": "PRIVATE_CALL_SIGNATURE", "providerExtra": "PRIVATE_UNKNOWN"},
            {"type": "image", "data": "synthetic-base64", "mimeType": "image/png", "signature": "PRIVATE_IMAGE_SIGNATURE"},
            {"type": "unknown_provider_block", "text": "PRIVATE_UNKNOWN_BLOCK"},
        ]},
        {"role": "assistant", "channel": "analysis", "content": "PRIVATE_CHANNEL"},
        {"role": "reasoning", "content": "PRIVATE_ROLE"},
        {"role": "toolResult", "toolName": "read", "toolCallId": "real-call", "content": [{"type": "text", "text": "public", "thinkingSignature": "PRIVATE_TOOL_SIGNATURE"}]},
    ])
    assert module.recover_episode(data, episode)["event_count"] == 2
    _, runtime, events = read(data, episode)
    serialized = json.dumps(events)
    assert "PRIVATE_" not in serialized
    assert "alice@example.org" in serialized and "/home/team/data" in serialized
    blocks = events[0]["payload"]["messages"][0]["content"]
    assert blocks[1]["arguments"] == arguments
    assert blocks[2] == {"type": "image", "data": "synthetic-base64", "mimeType": "image/png"}
    assert runtime["recovery"]["omitted_messages"] == {"private_message": 2}
    assert runtime["recovery"]["unsupported_content_blocks_excluded"] == 1


@pytest.mark.parametrize("mutation", ["complete", "nonempty", "observed", "v2", "unknown_role"])
def test_existing_samples_records_and_observations_untouched(training, mutation):
    data, _, _ = training
    episode = legacy(training)
    native(training)
    with data.analytics._db() as db:
        if mutation == "complete":
            db.execute("UPDATE training_tool_episodes SET state='complete',record='[{\"kind\":\"agent_end\"}]' WHERE id=?", (episode,))
        elif mutation == "nonempty":
            db.execute("UPDATE training_tool_episodes SET record='[{\"kind\":\"context\"}]' WHERE id=?", (episode,))
        elif mutation == "observed":
            db.execute("INSERT INTO training_observed_events(episode_id,sequence,kind,observed_at,payload) VALUES(?,0,'context',1,'{}')", (episode,))
        else:
            runtime = {"capture_policy": OBSERVED_POLICY, "run_label": "planner.cycle0"} if mutation == "v2" else {"run_label": "unknown-role"}
            db.execute("UPDATE training_tool_episodes SET runtime_metadata=? WHERE id=?", (json.dumps(runtime), episode))
    before = snapshot(data, episode)
    assert module.recover_episode(data, episode)["status"] == "skipped"
    assert snapshot(data, episode) == before


@pytest.mark.parametrize("change", ["revoke", "regrant", "disabled", "expired", "tombstone", "project_missing", "notice", "retention"])
def test_authorization_and_retention_reject_without_pruning(training, change):
    data, store, now = training
    episode = legacy(training)
    native(training)
    with data.analytics._db() as db:
        if change == "revoke":
            db.execute("UPDATE training_permissions SET granted=0")
        elif change == "regrant":
            db.execute("UPDATE training_permissions SET granted_at=granted_at+1")
        elif change == "tombstone":
            db.execute("DROP TRIGGER training_tool_delete")
            db.execute("INSERT INTO journey_tombstones(tenant_id,sid,deleted_at) VALUES('tenant-one','s-project',?)", (now[0],))
        elif change == "project_missing":
            db.execute("DELETE FROM journey_projects WHERE tenant_id='tenant-one' AND sid='s-project'")
        elif change == "notice":
            db.execute("DELETE FROM consents WHERE tenant_id='tenant-one'")
    if change == "disabled":
        store.set_access("tenant-one", enabled=False)
    elif change == "expired":
        store.set_access("tenant-one", enabled=True, expires_at=now[0])
    elif change == "retention":
        now[0] += 31 * 86400
    before = snapshot(data, episode)
    assert before[0] is not None
    assert module.recover_episode(data, episode)["status"] == "skipped"
    assert snapshot(data, episode) == before


@pytest.mark.parametrize("problem", ["missing", "other_tenant", "ambiguous", "header", "file_symlink", "directory_symlink", "hardlink"])
def test_unique_tenant_bound_source_required(training, tmp_path, problem):
    data, _, _ = training
    episode = legacy(training)
    if problem == "other_tenant":
        native(training, tenant="tenant-two")
    elif problem != "missing":
        path = native(training, header_id="87654321-1234-7234-8234-123456789abc" if problem == "header" else None)
        if problem == "ambiguous":
            path.with_name(f"duplicate_{SESSION}.jsonl").write_bytes(path.read_bytes())
        elif problem == "file_symlink":
            outside = tmp_path / "outside.jsonl"
            path.rename(outside)
            path.symlink_to(outside)
        elif problem == "directory_symlink":
            directory = path.parent
            outside = tmp_path / "outside-sessions"
            directory.rename(outside)
            directory.symlink_to(outside, target_is_directory=True)
        elif problem == "hardlink":
            (tmp_path / "outside-hardlink.jsonl").hardlink_to(path)
    before = snapshot(data, episode)
    assert module.recover_episode(data, episode)["status"] == "skipped"
    assert snapshot(data, episode) == before


@pytest.mark.parametrize("stage", ["during_read", "before_write", "before_commit"])
def test_changing_source_rolls_back_all_recovery(training, monkeypatch, stage):
    data, _, _ = training
    episode = legacy(training)
    path = native(training)
    original_check, checks = module._SessionSource.check, []
    target = {"during_read": 1, "before_write": 2, "before_commit": 3}[stage]

    def changing_check(source):
        checks.append(1)
        if len(checks) == target:
            with path.open("ab") as stream:
                stream.write(b"{}\n")
        return original_check(source)

    monkeypatch.setattr(module._SessionSource, "check", changing_check)
    before = snapshot(data, episode)
    assert module.recover_episode(data, episode)["reason"] == "session_file_changed"
    assert snapshot(data, episode) == before


@pytest.mark.parametrize("change", ["grant", "state", "metadata", "project"])
def test_write_transaction_rechecks_authorization_and_episode(training, monkeypatch, change):
    data, _, _ = training
    episode = legacy(training)
    native(training)
    original_read = module._SessionSource.read
    raced = []

    def changing_read(source, *args):
        result = original_read(source, *args)
        with data.analytics._db() as db:
            if change == "grant":
                db.execute("UPDATE training_permissions SET granted_at=granted_at+1")
            elif change == "state":
                db.execute("UPDATE training_tool_episodes SET state='complete' WHERE id=?", (episode,))
            elif change == "metadata":
                db.execute("UPDATE training_tool_episodes SET reason='changed_concurrently' WHERE id=?", (episode,))
            else:
                db.execute("DELETE FROM journey_projects WHERE tenant_id='tenant-one' AND sid='s-project'")
        raced.append(snapshot(data, episode))
        return result

    monkeypatch.setattr(module._SessionSource, "read", changing_read)
    assert module.recover_episode(data, episode)["status"] == "skipped"
    assert snapshot(data, episode) == raced[0]


def test_message_time_bounds_are_explicit_and_actual_session_scope_is_preserved(training):
    data, _, now = training
    episode = legacy(training)
    base = now[0]
    path = native(training, [
        {"role": "user", "content": "Before consent", "timestamp": (base - 10) * 1000},
        {"role": "assistant", "content": "Eligible resumed output", "timestamp": (base + 1) * 1000},
        {"role": "assistant", "content": "Future", "timestamp": (base + 10000) * 1000},
    ])
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "model_change", "modelId": "synthetic-model"}) + "\n")
    assert module.recover_episode(data, episode)["event_count"] == 1
    _, runtime, events = read(data, episode)
    assert events[0]["payload"]["messages"][0]["content"] == "Eligible resumed output"
    assert runtime["recovery"]["omitted_messages"] == {"before_authorization_or_retention": 1, "future_timestamp": 1}
    assert runtime["recovery"]["ignored_entries"] == {"model_change": 1}


def test_retention_cutoff_can_advance_while_recent_messages_are_read(training, monkeypatch):
    data, _, now = training
    episode = legacy(training)
    native(training)
    old_grant = now[0] - 31 * 86400
    with data.analytics._db() as db:
        db.execute("UPDATE training_permissions SET granted_at=? WHERE granted=1", (old_grant,))
        db.execute("UPDATE training_tool_episodes SET grants=? WHERE id=?", (json.dumps({"internal_training": old_grant}), episode))
    original_read = module._SessionSource.read

    def advancing_read(source, *args):
        result = original_read(source, *args)
        now[0] += 0.25
        return result

    monkeypatch.setattr(module._SessionSource, "read", advancing_read)
    assert module.recover_episode(data, episode)["status"] == "recovered"


@pytest.mark.parametrize("problem", ["truncated", "invalid_timestamp", "naive_timestamp", "duplicate_header", "duplicate_key", "nonfinite", "empty"])
def test_malformed_sources_are_not_partially_imported(training, problem):
    data, _, now = training
    episode = legacy(training)
    path = native(training)
    if problem == "empty":
        path.write_text("")
    else:
        extra = {
            "truncated": '{"type":',
            "invalid_timestamp": json.dumps({"type": "message", "timestamp": "bad", "message": {"role": "user", "content": "bad"}}),
            "naive_timestamp": json.dumps({"type": "message", "timestamp": "2033-05-18T03:33:21", "message": {"role": "user", "content": "bad"}}),
            "duplicate_header": json.dumps({"type": "session", "id": SESSION, "timestamp": iso(now[0])}),
            "duplicate_key": '{"type":"message","type":"session"}',
            "nonfinite": '{"type":"message","timestamp":NaN}',
        }[problem]
        with path.open("a") as stream:
            stream.write(extra + "\n")
    before = snapshot(data, episode)
    assert module.recover_episode(data, episode)["status"] == "skipped"
    assert snapshot(data, episode) == before


@pytest.mark.parametrize("episode_id", [True, False, 0, -1, 2**63, "1", None])
def test_invalid_episode_id_rejected(training, episode_id):
    with pytest.raises(ValueError, match="positive episode"):
        module.recover_episode(training[0], episode_id)
