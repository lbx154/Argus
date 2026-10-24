from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager

import pytest

from argus.manager import front_door
from argus.manager._session_ops import (
    ManagerLockCancelled,
    ManagerPipelineWaitTimeout,
    manager_pipeline_lock,
    manager_pipeline_yield_requested,
)


@contextmanager
def held_pipeline(root, *, release_on_request=False):
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with manager_pipeline_lock(root):
            entered.set()
            deadline = time.monotonic() + 10
            while not release.wait(0.01):
                if release_on_request and manager_pipeline_yield_requested(root):
                    break
                if time.monotonic() >= deadline:
                    break

    owner = threading.Thread(target=lambda: contextvars.Context().run(hold))
    owner.start()
    assert entered.wait(2)
    try:
        yield owner
    finally:
        release.set()
        owner.join(2)
        assert not owner.is_alive()


@pytest.mark.parametrize("cancel", [False, True])
def test_pipeline_wait_is_bounded_without_breaking_the_peer_lock(tmp_path, cancel):
    with held_pipeline(tmp_path) as owner:
        started = time.monotonic()
        cancelled = (lambda: time.monotonic() - started > 0.05) if cancel else None
        expected = ManagerLockCancelled if cancel else ManagerPipelineWaitTimeout
        with pytest.raises(expected):
            with manager_pipeline_lock(tmp_path, timeout=2 if cancel else 0.05, cancelled=cancelled):
                pytest.fail("contended lock entered")
        assert time.monotonic() - started < 1
        assert owner.is_alive()
    assert (tmp_path / ".manager_pipeline.lock").exists()
    with manager_pipeline_lock(tmp_path, timeout=0):
        pass


def test_delegated_gate_timeout_does_not_release_the_parent_file_lock(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    with manager_pipeline_lock(tmp_path):
        def nested():
            with manager_pipeline_lock(tmp_path):
                entered.set()
                release.wait(3)
        owner = threading.Thread(target=contextvars.copy_context().run, args=(nested,))
        owner.start()
        assert entered.wait(1)
        try:
            with pytest.raises(ManagerPipelineWaitTimeout):
                with manager_pipeline_lock(tmp_path, timeout=0.05):
                    pytest.fail("delegated gate entered")
        finally:
            release.set()
            owner.join(1)
    with manager_pipeline_lock(tmp_path, timeout=0):
        pass


@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.5", "3601"])
def test_operator_wait_budget_is_finite_and_validated(monkeypatch, value):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_HANDOFF_WAIT_SECONDS", value)
    with pytest.raises(ValueError):
        front_door._handoff_wait_seconds()
