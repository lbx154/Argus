"""Synthetic retained observations; no live accounts, providers, or source logs."""
import json
import threading

import pytest
from test_training_data import chat, grant, pi_observations
from test_training_data import training as training

from argus_skill.trial.analytics import AnalyticsError
from argus_skill.trial.collaboration_data import CollaborationData
from argus_skill.trial.training_data import _hash, _json


def event(training, event_type, *, tenant="tenant-one", task="task-real", **payload):
    data, _, now = training
    now[0] += 1
    data.journal.poll(tenant)
    with data.analytics._db() as db:
        data.journal._insert(
            db, tenant, "s-project", "runtime_event", {"fixture": now[0]}, event_type,
            {"type": event_type, **({"item_id": task} if task else {}), **payload}, source_timestamp=now[0],
        )


def episode(training, *, tenant="tenant-one", task="task-real", role="engineer-r1", complete=True):
    data, _, now = training
    now[0] += 1
    data.journal.poll(tenant)
    episode_id = data.capture.begin(
        tenant, "s-project", f"fixture-{int(now[0])}", observer_verified=True, allowed_tools=["sum_numbers"],
        runtime_metadata={"run_label": role, "mission_id": task},
    )["episode_id"]
    if complete:
        for kind, payload in pi_observations(now[0]):
            result = data.capture.event(tenant, "s-project", episode_id, kind, payload)
        assert result["state"] == "complete"
    return episode_id


def test_original_request_actual_roles_and_no_invented_handoffs(training):
    data, _, now = training
    grant(data)
    chat(training, task="task-real", text="Prove the finite sum identity and check 100 cases.")
    event(training, "life.mission.started", title="Finite sum proof", objective="Prove the identity rigorously.")
    event(training, "engineer.progress", agent_layer="engineer", actor="engineer-r1", text="Checking the identity.")
    episode_id = episode(training)
    event(training, "engineer.progress", agent_layer="reviewer", actor="reviewer", text="Checking the proof.")
    event(training, "life.mission.completed", status="done", success=True)
    view = CollaborationData(data)
    before = now[0]
    detail = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    assert detail["title"] == "Prove the finite sum identity and check 100 cases."
    assert detail["request"]["association"] == "authoritative"
    assert detail["mission_title"] == "Finite sum proof"
    assert detail["objective"] == "Prove the identity rigorously."
    assert detail["mission_brief_source"]["kind"] == "life.mission.started"
    assert detail["mission_brief_source"]["association"] == "authoritative"
    assert detail["mission_brief_source"]["event_id"] != detail["request"]["event_id"]
    assert {item["role"] for item in detail["roles"]} == {"engineer", "reviewer", "unknown"}
    assert detail["handoffs"] == []
    assert detail["task_outcome"]["state"] == "complete"
    assert detail["quality"] == {"approved_samples": 0, "candidates": 1}
    assert detail["collection"]["accepted_episodes"] == 1
    assert detail["episodes"][0]["episode_id"] == episode_id
    assert detail["episodes"][0]["tool_pairs"] == [{
        "call_id": "call-real", "name": "sum_numbers", "status": "success",
        "call_timestamp": before - 2, "result_timestamp": before - 2, "result_characters": 1,
    }]
    assert "Add two and three" not in json.dumps(detail)  # No injected role prompts or tool output.
    assert detail["global_complete"] is False
    overview = view.overview(tenant="tenant-one")
    assert overview["counts"] == {"tasks": 1, "roles": 2, "tool_pairs": 1, "approved_samples": 0, "candidates": 1,
                                   "observed_episodes": 1, "observed_events": 5}
    assert not any("episodes" in task for task in overview["tasks"])


def test_nearby_rows_role_words_and_other_tasks_never_become_associations(training):
    data, _, _ = training
    grant(data)
    chat(training, text="Manager must delegate to Engineer and Reviewer.")
    event(training, "life.mission.started", task="task-one", title="First task")
    event(training, "engineer.progress", task=None, text="Manager Engineer Reviewer")
    event(training, "engineer.progress", task="task-two", agent_layer="reviewer")
    episode(training, task="task-one", role="arbitrary-runtime-label")
    view = CollaborationData(data)
    first = view.detail("internal_training", "tenant-one", "s-project", "task-one")
    assert first["request"] is None
    assert first["title"] == "First task"
    assert {role["role"] for role in first["roles"]} == {"unknown"}
    assert first["task_outcome"]["state"] == "unknown"
    assert first["task_outcome"]["last_lifecycle"]["state"] == "start"
    second = view.detail("internal_training", "tenant-one", "s-project", "task-two")
    assert [role["role"] for role in second["roles"]] == ["reviewer"]
    unassigned = view.detail("internal_training", "tenant-one", "s-project")
    assert unassigned["request"]["association"] == "unassigned"
    assert unassigned["task_id"] is None
    assert unassigned["mission_title"] is unassigned["objective"] is unassigned["mission_brief_source"] is None
    assert {role["role"] for role in unassigned["roles"]} == {"unknown"}
    assert first["unassigned_observations"] > 0
    assert first["handoffs"] == second["handoffs"] == []


def test_revocation_regrant_purpose_and_cross_tenant_are_enforced(training):
    data, _, now = training
    grant(data)
    grant(data, tenant="tenant-two", external=True)
    chat(training, task="shared-task", text="Tenant one original request")
    chat(training, tenant="tenant-two", task="shared-task", text="Tenant two original request")
    episode(training, task="shared-task")
    episode(training, tenant="tenant-two", task="shared-task", role="reviewer")
    view = CollaborationData(data)
    detail = view.detail("internal_training", "tenant-one", "s-project", "shared-task")
    assert "Tenant two" not in json.dumps(detail)
    assert {role["role"] for role in detail["roles"]} == {"unknown", "engineer"}
    assert all(row["tenant_id"] == "tenant-one" for row in view.overview(tenant="tenant-one")["tasks"])
    with pytest.raises(AnalyticsError, match="purpose_consent_required"):
        view.detail("external_sharing", "tenant-one", "s-project", "shared-task")
    grant(data, internal=False)
    denied = view.overview(tenant="tenant-one")
    assert denied["tasks"] == []
    assert denied["projects"][0]["reason"] == "purpose_consent_required"
    now[0] += 1
    grant(data)
    assert view.overview(tenant="tenant-one")["tasks"] == []
    assert view.overview(tenant="tenant-two")["counts"]["tool_pairs"] == 1


def test_delete_inactive_access_and_retention_do_not_reveal_titles_or_counts(training):
    data, store, now = training
    grant(data)
    chat(training, task="task-real", text="Sensitive title to remove")
    episode(training)
    view = CollaborationData(data)
    store.set_access("tenant-one", enabled=False)
    assert view.overview(tenant="tenant-one")["tasks"] == []
    store.set_access("tenant-one", enabled=True)
    now[0] += 31 * 86400
    assert view.overview(tenant="tenant-one")["tasks"] == []
    chat(training, task="task-later", text="Later request")
    data.controls.delete_copies(data.journal, "tenant-one", "s-project")
    result = view.overview(tenant="tenant-one")
    assert result["tasks"] == []
    assert result["projects"] == []
    with pytest.raises(AnalyticsError, match="research_deleted"):
        view.detail("internal_training", "tenant-one", "s-project", "task-later")
    assert "Later request" not in json.dumps(result)


def test_quarantined_metadata_has_no_content_and_never_implies_task_failure(training):
    data, _, _ = training
    grant(data)
    event(training, "life.mission.started", title="Optimization task")
    episode_id = episode(training, role="reviewer", complete=False)
    data.capture.event("tenant-one", "s-project", episode_id, "context", {
        "messages": [{"role": "user", "content": "password=should-never-appear"}], "tools": [],
    })
    with data.analytics._db() as db:
        # Quarantine is never read even if an old/corrupt record retained content.
        db.execute("UPDATE training_tool_episodes SET record=? WHERE id=?", ('PRIVATE_QUARANTINE_SENTINEL', episode_id))
    detail = CollaborationData(data).detail("internal_training", "tenant-one", "s-project", "task-real")
    serialized = json.dumps(detail)
    assert "should-never-appear" not in serialized and "PRIVATE_QUARANTINE_SENTINEL" not in serialized
    assert detail["task_outcome"]["state"] == "unknown"
    assert detail["task_outcome"]["last_lifecycle"]["state"] == "start"
    assert detail["quality"] == {"approved_samples": 0, "candidates": 0}
    assert detail["collection"]["quarantined_episodes"] == 1
    assert detail["episodes"][0]["tool_pairs"] == []
    assert detail["episodes"][0]["role"] == "reviewer"


def test_auto_review_is_exactly_bound_and_read_only(training):
    data, _, now = training
    grant(data)
    episode_id = episode(training)
    key = _hash(_json(["pi_episode", "tenant-one", "s-project", episode_id]).encode())
    data.export("internal_training", [{"tenant_id": "tenant-one", "sid": "s-project"}], review={
        "content_approved": True, "tool_context_approved": True, "approved_event_ids": [key],
        "reviewer_kind": "automated_acceptance", "evidence_sha256": "a" * 64,
    })
    view = CollaborationData(data)
    detail = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    evidence = detail["episodes"][0]["quality_evidence"]
    assert detail["quality"]["approved_samples"] == 1
    assert evidence["human_reviewed"] is False
    assert evidence["reviewer_kind"] == "automated_acceptance"
    with data.analytics._db() as db:
        db.execute("UPDATE training_sample_reviews SET sample_sha256=?", ("b" * 64,))
        before = dict(db.execute("SELECT * FROM training_sample_reviews").fetchone())
    assert view.detail("internal_training", "tenant-one", "s-project", "task-real")["quality"]["approved_samples"] == 0
    with data.analytics._db() as db:
        after = dict(db.execute("SELECT * FROM training_sample_reviews").fetchone())
    assert before == after  # Rendering never prunes or rewrites review receipts.
    assert now[0] == evidence["reviewed_at"]


def test_malformed_cross_tenant_record_never_enters_task_projection(training):
    data, _, _ = training
    grant(data)
    event(training, "life.mission.started", title="Valid task")
    with data.analytics._db() as db:
        row = db.execute("SELECT id,record FROM journey_events WHERE record LIKE '%Valid task%'").fetchone()
        record = json.loads(row["record"])
        record["tenant_id"] = "tenant-two"
        record["payload"]["title"] = "CROSS_TENANT_SENTINEL"
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (json.dumps(record), row["id"]))
    result = CollaborationData(data).overview(tenant="tenant-one")
    assert "CROSS_TENANT_SENTINEL" not in json.dumps(result)
    assert result["counts"]["tasks"] == 0
    assert result["tasks"][0]["collection"]["gaps"] >= 1


def test_conflicting_role_metadata_stays_unknown_and_tool_calls_are_not_progress_counts(training):
    data, _, _ = training
    grant(data)
    event(training, "engineer.progress", agent_layer="reviewer", actor="engineer-r1", kind="tool_call", tool_name="bash")
    detail = CollaborationData(data).detail("internal_training", "tenant-one", "s-project", "task-real")
    assert detail["roles"] == [{"role": "unknown", "label": "角色未记录", "observations": 1,
                                 "episodes": 0, "tool_pairs": 0}]
    assert detail["episodes"] == []


def test_summary_cache_never_reuses_changed_content_or_revoked_grants(training):
    data, _, _ = training
    grant(data)
    episode_id = episode(training)
    view = CollaborationData(data)
    first = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    assert first["quality"]["candidates"] == 1
    with data.analytics._db() as db:
        db.execute("UPDATE training_tool_episodes SET record=? WHERE id=?", ('PRIVATE_CHANGED_CONTENT', episode_id))
    later = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    assert later["quality"]["candidates"] == 0
    assert later["episodes"][0]["reason"] == "malformed_tool_episode"
    assert "PRIVATE_CHANGED_CONTENT" not in json.dumps(later)
    grant(data, internal=False)
    with pytest.raises(AnalyticsError, match="purpose_consent_required"):
        view.detail("internal_training", "tenant-one", "s-project", "task-real")


def test_view_byte_limit_never_reads_oversized_completed_content(training, monkeypatch):
    from argus_skill.trial import collaboration_data

    data, _, _ = training
    grant(data)
    episode(training)
    monkeypatch.setattr(collaboration_data, "MAX_OVERVIEW_SOURCE_BYTES", 1)
    result = CollaborationData(data).overview(tenant="tenant-one")
    assert result["counts"]["candidates"] == result["counts"]["tool_pairs"] == 0
    task = next(row for row in result["tasks"] if row["task_id"] == "task-real")
    assert task["collection"]["states"]["complete"] == 1
    assert result["completeness"]["page_truncated"] is True
    assert result["completeness"]["reason_counts"]["display_byte_limit"] >= 1
    assert result["completeness"]["source_bytes_examined"] <= 1


def test_revocation_during_projection_is_rechecked_before_content_leaves(training, monkeypatch):
    data, _, _ = training
    grant(data)
    episode(training)
    view = CollaborationData(data)
    original = view._episode

    def revoke_after_snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        grant(data, internal=False)
        return result

    monkeypatch.setattr(view, "_episode", revoke_after_snapshot)
    result = view.overview(tenant="tenant-one")
    assert result["tasks"] == []
    assert result["counts"]["tool_pairs"] == 0
    assert result["projects"][0]["reason"] == "purpose_consent_required"
    assert result["projects"][0]["title"] == "s-project"


def test_expensive_validation_does_not_block_new_capture(training, monkeypatch):
    data, _, _ = training
    grant(data)
    episode(training)
    view = CollaborationData(data)
    original = view._episode
    available = []

    def check_capture_lock(*args, **kwargs):
        def acquire():
            acquired = data.controls.capture_lock.acquire(timeout=0.2)
            available.append(acquired)
            if acquired:
                data.controls.capture_lock.release()

        worker = threading.Thread(target=acquire)
        worker.start()
        worker.join()
        return original(*args, **kwargs)

    monkeypatch.setattr(view, "_episode", check_capture_lock)
    result = view.overview(tenant="tenant-one")
    assert result["counts"]["tool_pairs"] == 1
    assert available == [True]


def test_sensitive_values_cannot_escape_in_status_or_quarantine_reason(training):
    data, _, _ = training
    grant(data)
    event(training, "engineer.progress", agent_layer="engineer", status="running")
    episode_id = episode(training, role="reviewer", complete=False)
    secret = "sk-proj-syntheticstatusmustnotleak"
    with data.analytics._db() as db:
        row = db.execute("SELECT sequence,record FROM journey_events WHERE gap=0 ORDER BY sequence DESC LIMIT 1").fetchone()
        record = json.loads(row["record"])
        record["payload"]["status"] = secret
        db.execute("UPDATE journey_events SET record=? WHERE sequence=?", (json.dumps(record), row["sequence"]))
        db.execute("UPDATE training_tool_episodes SET state='quarantined',reason=? WHERE id=?", (secret, episode_id))
    result = CollaborationData(data).detail("internal_training", "tenant-one", "s-project", "task-real")
    assert secret not in json.dumps(result)
    assert result["episodes"][0]["reason"] == "capture_not_settled"
    assert any(item["content_withheld"] for item in result["segments"] if item["source_kind"] == "journal_event")


def test_known_tool_activity_is_useful_without_private_body_or_unknown_names(training):
    data, _, _ = training
    grant(data)
    labels = {"read": "读取文件", "write": "写入文件", "edit": "修改文件", "bash": "执行命令",
              "grep": "搜索内容", "find": "查找文件", "ls": "查看目录"}
    unknown = ["private_tool_name_sentinel", "sk-proj-toolnamemustnotleak", "read /private/file"]
    for tool in [*labels, *unknown]:
        event(training, "engineer.progress", agent_layer="reviewer", actor="reviewer", kind="tool_call",
              tool_name=tool, status="failed", text="password=private-tool-body-sentinel")
    result = CollaborationData(data).detail("internal_training", "tenant-one", "s-project", "task-real")
    segments = result["segments"]
    assert [item["tool_name"] for item in segments] == [*labels, None, None, None]
    assert [item["summary"] for item in segments[:len(labels)]] == [f"工具活动：{label}" for label in labels.values()]
    assert all(item["status"] == "failed" and item["tool_pairs"] == 0 for item in segments)
    assert all(item["content_withheld"] for item in segments)
    assert result["task_outcome"]["state"] == "unknown"
    assert result["quality"] == {"approved_samples": 0, "candidates": 0}
    text = json.dumps(result)
    assert "private-tool-body-sentinel" not in text
    assert all(name not in text for name in unknown)


def test_overview_budget_is_independent_of_export_and_project_detail(training, monkeypatch):
    from argus_skill.trial import collaboration_data

    data, _, _ = training
    grant(data)
    episode(training)
    monkeypatch.setattr(collaboration_data, "MAX_SOURCE_BYTES", 1)
    view = CollaborationData(data)
    overview = view.overview(tenant="tenant-one")
    assert overview["counts"]["candidates"] == 1
    assert overview["completeness"]["source_byte_limit"] == 128 * 1024 * 1024
    assert overview["completeness"]["page_truncated"] is False
    detail = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    assert detail["quality"]["candidates"] == 0
    assert detail["completeness"]["page_truncated"] is True
    assert detail["completeness"]["source_byte_limit"] == 1


def test_per_episode_bound_still_applies_to_larger_overview_budget(training, monkeypatch):
    from argus_skill.trial import collaboration_data

    data, _, _ = training
    grant(data)
    episode(training)
    monkeypatch.setattr(collaboration_data, "MAX_EPISODE_BYTES", 1)
    result = CollaborationData(data).overview(tenant="tenant-one")
    assert result["counts"]["candidates"] == 0
    assert result["completeness"]["page_truncated"] is True
    assert result["completeness"]["reason_counts"]["display_episode_size_limit"] == 1


def test_event_page_truncation_is_visible_without_dropping_other_project_samples(training, monkeypatch):
    from argus_skill.trial import collaboration_data

    data, _, _ = training
    grant(data)
    grant(data, tenant="tenant-two")
    event(training, "life.mission.started", title="Older task")
    event(training, "engineer.progress", agent_layer="engineer")
    episode(training, tenant="tenant-two", task="other-task")
    monkeypatch.setattr(collaboration_data, "MAX_PROJECT_EVENTS", 1)
    result = CollaborationData(data).overview()
    assert result["counts"]["candidates"] == 1
    assert result["completeness"]["page_truncated"] is True
    assert result["completeness"]["reason_counts"]["display_event_limit"] == 1
    assert any(task["task_id"] == "other-task" and task["quality"]["candidates"] == 1 for task in result["tasks"])
