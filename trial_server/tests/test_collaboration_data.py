"""Synthetic retained observations; no live accounts, providers, or source logs."""
import json

import pytest
from test_training_data import grant, pi_observations
from test_training_data import training as training

from argus_trial_server.analytics import AnalyticsError
from argus_trial_server.collaboration_data import CollaborationData
from argus_trial_server.training_data import _key


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
    key = _key("pi_episode", "tenant-one", "s-project", episode_id)
    data.export("internal_training", [{"tenant_id": "tenant-one", "sid": "s-project"}], review={
        "content_approved": True, "tool_context_approved": True, "approved_event_ids": [key],
        "reviewer_kind": "automated_acceptance", "evidence_bytes": 64,
    })
    view = CollaborationData(data)
    detail = view.detail("internal_training", "tenant-one", "s-project", "task-real")
    evidence = detail["episodes"][0]["quality_evidence"]
    assert detail["quality"]["approved_samples"] == 1
    assert evidence["human_reviewed"] is False
    assert evidence["reviewer_kind"] == "automated_acceptance"
    with data.analytics._db() as db:
        db.execute("UPDATE training_sample_reviews SET sample_bytes=sample_bytes+1")
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
    from argus_trial_server import collaboration_data

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


def test_overview_budget_is_independent_of_export_and_project_detail(training, monkeypatch):
    from argus_trial_server import collaboration_data

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
    from argus_trial_server import collaboration_data

    data, _, _ = training
    grant(data)
    episode(training)
    monkeypatch.setattr(collaboration_data, "MAX_EPISODE_BYTES", 1)
    result = CollaborationData(data).overview(tenant="tenant-one")
    assert result["counts"]["candidates"] == 0
    assert result["completeness"]["page_truncated"] is True
    assert result["completeness"]["reason_counts"]["display_episode_size_limit"] == 1


def test_event_page_truncation_is_visible_without_dropping_other_project_samples(training, monkeypatch):
    from argus_trial_server import collaboration_data

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
