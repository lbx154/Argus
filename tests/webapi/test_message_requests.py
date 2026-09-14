from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.webapi.message_requests import (
    MessageRequestCancelled,
    MessageRequestCapacityError,
    MessageRequestConflict,
    MessageRequestRegistry,
)


def test_cancel_before_http_intake_prevents_the_worker_from_running():
    registry = MessageRequestRegistry()
    response = registry.cancel("project-a", "request-1")
    assert response == {"requested": True, "sid": "project-a", "request_id": "request-1", "status": "cancelled", "active": False}
    ran = []
    with pytest.raises(MessageRequestCancelled):
        with registry.message_request("project-a", "request-1"):
            ran.append(True)
    assert ran == []


def test_http_registration_can_be_finished_in_the_worker_thread():
    registry = MessageRequestRegistry()
    lease = registry.begin("project-a", "request-1")
    entered = threading.Event()
    release = threading.Event()

    def worker():
        try:
            entered.set()
            assert release.wait(1)
            return lease.cancelled()
        finally:
            lease.finish()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(worker)
        assert entered.wait(1)
        registry.cancel("project-a", lease.request_id)
        release.set()
        assert result.result(timeout=1)
    with pytest.raises(MessageRequestCancelled):
        registry.begin("project-a", "request-1")


def test_duplicate_active_request_rejects_without_cancelling_the_original():
    registry = MessageRequestRegistry()
    with registry.begin("project-a", "same") as lease:
        with pytest.raises(MessageRequestConflict) as error:
            registry.begin("project-a", "same")
        assert error.value.status_code == 409
        assert not lease.cancelled()


def test_late_cancellation_is_scoped_to_both_project_and_request_identity():
    registry = MessageRequestRegistry()
    with registry.begin("project-a", "old"):
        pass
    current = registry.begin("project-a", "new")
    other_project = registry.begin("project-b", "new")
    finished = registry.cancel("project-a", "old")
    assert finished["status"] == "finished" and finished["requested"] is False
    assert not current.cancelled() and not other_project.cancelled()
    assert registry.cancel("project-a", "new")["active"] is True
    assert current.cancelled() and not other_project.cancelled()
    current.finish()
    other_project.finish()


def test_active_callbacks_survive_time_and_capacity_pressure():
    now = [0.0]
    registry = MessageRequestRegistry(max_active=1, max_entries=2, tombstone_ttl=2, clock=lambda: now[0])
    lease = registry.begin("project-a", "running")
    cancelled = lease.cancelled
    registry.cancel("project-a", "arrives-later")
    with pytest.raises(MessageRequestCapacityError) as error:
        registry.begin("project-a", "overflow")
    assert error.value.status_code == 503
    with pytest.raises(MessageRequestCapacityError):
        registry.cancel("project-a", "another-future-request")
    with pytest.raises(MessageRequestCancelled):
        registry.begin("project-a", "arrives-later")
    now[0] = 100.0
    with pytest.raises(MessageRequestCapacityError):
        registry.begin("project-a", "overflow")
    registry.cancel("project-a", "running")
    assert cancelled(), "capacity cleanup must not detach the provider's existing callback"
    lease.finish()
    with registry.begin("project-a", "next") as next_request:
        assert not next_request.cancelled()


def test_context_exception_releases_active_capacity_and_preserves_finished_identity():
    registry = MessageRequestRegistry(max_active=1)
    with pytest.raises(RuntimeError, match="worker failed"):
        with registry.message_request("project-a", "failed-worker"):
            raise RuntimeError("worker failed")
    with registry.begin("project-a", "another"):
        pass
    with pytest.raises(MessageRequestConflict):
        registry.begin("project-a", "failed-worker")


def test_finish_is_idempotent_and_cannot_finish_a_new_lease_after_retention():
    now = [0.0]
    registry = MessageRequestRegistry(max_active=1, max_entries=1, tombstone_ttl=2, clock=lambda: now[0])
    old = registry.begin("project-a", "id")
    old.finish()
    with pytest.raises(MessageRequestCapacityError):
        registry.begin("project-a", "other")
    now[0] = 3.0
    current = registry.begin("project-a", "id")
    old.finish()
    registry.cancel("project-a", "id")
    assert current.cancelled() and not old.cancelled()
    current.finish()


def test_empty_registration_id_is_generated_and_registry_instances_are_isolated():
    first, second = MessageRequestRegistry(), MessageRequestRegistry()
    with first.begin("project-a") as generated:
        assert len(generated.request_id) == 32
        second.cancel("project-a", generated.request_id)
        assert not generated.cancelled()
    with pytest.raises(ValueError):
        first.cancel("project-a", "")


def test_cancel_and_registration_race_cannot_start_uncancellable_work():
    registry = MessageRequestRegistry()
    for number in range(32):
        barrier = threading.Barrier(2)
        request_id = str(number)

        def register():
            barrier.wait(timeout=1)
            try:
                return registry.begin("project-a", request_id)
            except MessageRequestCancelled:
                return None

        def cancel():
            barrier.wait(timeout=1)
            registry.cancel("project-a", request_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(register)
            cancellation = pool.submit(cancel)
            cancellation.result(timeout=1)
            lease = request.result(timeout=1)
        if lease is not None:
            assert lease.cancelled()
            lease.finish()
