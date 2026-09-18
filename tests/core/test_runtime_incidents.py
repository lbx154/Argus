from __future__ import annotations

from pathlib import Path

from argus.core.runtime_incidents import (
    RuntimeIncidentStore,
    drain_runtime_incident_events,
)


def _detect(store: RuntimeIncidentStore) -> dict:
    return store.detect(
        detector="watchdog",
        invariant="running_requires_live_executor",
        subject_kind="mission",
        subject_id="mission-1",
        severity="error",
        observed={"status": "running", "executor_alive": False},
    )


def test_verified_recovery_is_durable_without_manager_event(tmp_path: Path) -> None:
    store = RuntimeIncidentStore(tmp_path)
    incident = _detect(store)
    store.begin_recovery(
        incident["incident_id"],
        action="requeue_parent",
        expected_postcondition="parent is pending",
    )

    result = store.verify_recovery(
        incident["incident_id"],
        recovered=True,
        evidence={"status": "pending"},
    )

    assert result["status"] == "recovered"
    assert result["recovery_verified"] is True
    assert result["pending_event"] is None
    assert store.pending_events() == []


def test_failed_recovery_escalates_once_and_requires_ack(tmp_path: Path) -> None:
    store = RuntimeIncidentStore(tmp_path)
    incident = _detect(store)
    store.begin_recovery(
        incident["incident_id"],
        action="requeue_parent",
        expected_postcondition="parent is pending",
    )
    store.verify_recovery(
        incident["incident_id"],
        recovered=False,
        evidence={"status": "running"},
    )
    events: list[dict] = []

    assert drain_runtime_incident_events(tmp_path, events.append) == 1
    assert events[0]["type"] == "life.runtime.incident.escalated"
    assert events[0]["manager_attention_required"] is True
    assert drain_runtime_incident_events(tmp_path, events.append) == 0


def test_third_recovered_occurrence_escalates_as_repeated(tmp_path: Path) -> None:
    store = RuntimeIncidentStore(tmp_path)
    result = {}
    for _ in range(3):
        incident = _detect(store)
        store.begin_recovery(
            incident["incident_id"],
            action="requeue_parent",
            expected_postcondition="parent is pending",
        )
        result = store.verify_recovery(
            incident["incident_id"],
            recovered=True,
            evidence={"status": "pending"},
        )

    assert result["status"] == "escalated"
    assert result["recovery_verified"] is True
    assert result["occurrence_count"] == 3
    assert result["escalation_reason"] == "incident repeated after prior recovery"


def test_unresolved_incident_uses_bounded_escalation_threshold(
    tmp_path: Path,
) -> None:
    store = RuntimeIncidentStore(tmp_path)
    first = store.record_unresolved(
        detector="resource_lease",
        invariant="live_worker_renews_resource_grant",
        subject_kind="subagent",
        subject_id="worker-1",
        severity="error",
        observed={"renewed": False},
        reason="renewal failed",
        escalation_after=2,
    )
    second = store.record_unresolved(
        detector="resource_lease",
        invariant="live_worker_renews_resource_grant",
        subject_kind="subagent",
        subject_id="worker-1",
        severity="error",
        observed={"renewed": False},
        reason="renewal failed",
        escalation_after=2,
    )

    assert first["status"] == "detected"
    assert second["status"] == "escalated"
    assert len(store.pending_events()) == 1


def test_successful_recovery_closes_escalated_unresolved_incident(
    tmp_path: Path,
) -> None:
    store = RuntimeIncidentStore(tmp_path)
    for _ in range(3):
        store.record_unresolved(
            detector="resource_lease",
            invariant="live_worker_renews_resource_grant",
            subject_kind="subagent",
            subject_id="worker-1",
            severity="error",
            observed={"renewed": False},
            reason="renewal failed",
            escalation_after=3,
        )

    result = store.recover_if_present(
        detector="resource_lease",
        invariant="live_worker_renews_resource_grant",
        subject_kind="subagent",
        subject_id="worker-1",
        action="renew_resource_grant",
        expected_postcondition="resource grant renewal succeeds",
        evidence={"renewed": True},
    )

    assert result is not None
    assert result["status"] == "recovered"
    assert result["recovery_verified"] is True
    assert result["pending_event"] is None
