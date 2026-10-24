from __future__ import annotations

import pytest

from argus.webapi.message_requests import (
    MessageRequestCancelled,
    MessageRequestCapacityError,
    MessageRequestConflict,
    MessageRequestRegistry,
)


def test_duplicate_active_request_rejects_without_cancelling_the_original():
    registry = MessageRequestRegistry()
    with registry.begin("project-a", "same") as lease:
        with pytest.raises(MessageRequestConflict) as error:
            registry.begin("project-a", "same")
        assert error.value.status_code == 409
        assert not lease.cancelled()


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
