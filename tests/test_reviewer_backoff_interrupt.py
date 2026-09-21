"""Stop a real SkillLoop during Reviewer retry without another provider call."""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.models import RunnerResult
from argus.core.run_gateway import run_interrupt_scope
from argus.engineer import round_execution, round_reviewer
from argus.engineer.runner import EngineerConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig

REVIEW_FAILURE = "Process exited with code 1 before turn completion."
ENGINEER_OUTPUT = "The requested local result is ready for independent review."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(sock, address)
        raise AssertionError("Backoff regressions cannot use an external network")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Backoff regressions cannot use a real provider or network")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)


class Backend:
    def __init__(self, role, interrupt=None):
        self.role = role
        self.calls = 0
        self._default_interrupt_reason_provider = interrupt

    def run_exec(self, **_kwargs):
        self.calls += 1
        reason = self._default_interrupt_reason_provider() if self._default_interrupt_reason_provider else None
        if reason:
            return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason)
        if self.role == "engineer":
            return RunnerResult(exit_code=0, agent_messages=[ENGINEER_OUTPUT])
        if self.calls == 1:
            return RunnerResult(exit_code=1, fatal_error=REVIEW_FAILURE)
        from argus.core.role_tool_bridge import bridge_request

        bridge_request("ARGUS_PLUGIN_REVIEW", "approve_review", {
            "review": "The local result was independently checked.",
        }, env=_kwargs["options"].extension_env)
        return RunnerResult(exit_code=0)


def make_loop(tmp_path, engineer, reviewer, events):
    return SkillLoop(
        skills_dir=tmp_path / "skills", engineer_runner=engineer, reviewer_runner=reviewer,
        config=SkillLoopConfig(
            engineer_model="offline", reviewer_model="offline", max_rounds=3,
            active_vertical="software", workflow_mode="direct", role_session_policy="fresh",
            require_post_task_learning=False, wiki_enabled=False, auto_init_wiki=False,
        ),
        on_event=events.append,
    )


@pytest.mark.parametrize("source", ["reviewer", "engineer", "scope"])
@pytest.mark.parametrize("reason,expected,stop_kind", [
    ("daemon stop requested", "paused_daemon_shutdown", "daemon_shutdown"),
    ("operator abort requested: replace the current target", "aborted", "operator_abort"),
])
def test_skill_loop_stops_during_reviewer_backoff_without_another_call(
    tmp_path, monkeypatch, source, reason, expected, stop_kind,
):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    sleeps, events = [], []
    consumed = []

    def interrupt():
        if not stopped.is_set() or consumed:
            return None
        consumed.append(reason)
        return reason

    engineer = Backend("engineer", interrupt if source == "engineer" else None)
    reviewer = Backend("reviewer", interrupt if source == "reviewer" else None)
    loop = make_loop(tmp_path, engineer, reviewer, events)

    def controlled_sleep(seconds):
        sleeps.append(seconds)
        entered.set()
        assert release.wait(4), "Test did not release the controlled first sleep slice"

    # On the original implementation this records the entire 15-second sleep.
    # The shared helper records a short slice and checks the signal immediately
    # after that slice. The gate avoids slow tests while preserving both paths.
    clock = SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=controlled_sleep)
    monkeypatch.setattr(round_reviewer, "time", clock)
    monkeypatch.setattr(round_execution, "time", clock)

    def run():
        scope = run_interrupt_scope(interrupt) if source == "scope" else nullcontext()
        with scope:
            return loop.run("Prepare a local result for independent review.", workdir=tmp_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run)
        try:
            assert entered.wait(3), "Real SkillLoop did not enter Reviewer backoff"
            stopped.set()
        finally:
            release.set()
        outcome = pending.result(timeout=2)

    assert outcome.status == expected
    assert engineer.calls == reviewer.calls == 1
    assert consumed == [reason], "The first stop reason must survive a consumptive callback"
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.2
    assert len(outcome.rounds) == 1
    record = outcome.rounds[0]
    assert record.engineer_exit_code == 0 and record.engineer_message == ENGINEER_OUTPUT
    assert record.review.backend_unavailable and record.review.status == "blocked"
    assert record.review.backend_fatal_error == REVIEW_FAILURE
    assert record.stop_kind == stop_kind and reason in outcome.reason
    interrupted = [event for event in events if event["type"] == "round.backend_failure.hold_interrupted"]
    assert len(interrupted) == 1 and interrupted[0]["stop_kind"] == stop_kind


def test_skill_loop_without_stop_keeps_full_backoff_and_retries_only_reviewer(tmp_path, monkeypatch):
    sleeps, events = [], []
    clock = SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=sleeps.append)
    monkeypatch.setattr(round_reviewer, "time", clock)
    monkeypatch.setattr(round_execution, "time", clock)
    engineer, reviewer = Backend("engineer"), Backend("reviewer")
    loop = make_loop(tmp_path, engineer, reviewer, events)
    outcome = loop.run("Prepare a local result for independent review.", workdir=tmp_path)
    assert outcome.status == "done"
    assert engineer.calls == 1 and reviewer.calls == 2
    assert len(outcome.rounds) == 1 and outcome.rounds[0].engineer_message == ENGINEER_OUTPUT
    assert sum(sleeps) == pytest.approx(loop.config.backend_failure_backoff_seconds)
    assert all(0 < seconds <= 0.2 for seconds in sleeps)


@pytest.mark.parametrize("reason", ["daemon stop requested", "operator abort requested: new target"])
def test_real_retry_sleep_returns_promptly_after_stop(tmp_path, monkeypatch, reason):
    entered, stopped = threading.Event(), threading.Event()
    events = []

    def observed_sleep(seconds):
        entered.set()
        time.sleep(seconds)

    clock = SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=observed_sleep)
    monkeypatch.setattr(round_reviewer, "time", clock)
    monkeypatch.setattr(round_execution, "time", clock)
    engineer = Backend("engineer")
    reviewer = Backend("reviewer", lambda: reason if stopped.is_set() else None)
    loop = make_loop(tmp_path, engineer, reviewer, events)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(loop.run, "Prepare a local result for independent review.", workdir=tmp_path)
        assert entered.wait(3)
        requested_at = time.monotonic()
        stopped.set()
        outcome = pending.result(timeout=0.75)
        elapsed = time.monotonic() - requested_at
    assert elapsed < 0.75
    assert outcome.status in {"paused_daemon_shutdown", "aborted"}
    assert engineer.calls == reviewer.calls == 1
    assert outcome.rounds[0].engineer_message == ENGINEER_OUTPUT


def hold_engine(engineer):
    return SupervisedEngineer(engineer_runner=engineer, reviewer=SimpleNamespace(),
                              engineer_config=EngineerConfig(model="offline"),
                              reviewer_config=ReviewerConfig(model="offline"))


def test_shared_bound_callback_is_polled_once_per_check():
    calls = []

    class Signal:
        def poll(self):
            calls.append(True)
            return None

    signal = Signal()
    engineer = SimpleNamespace(_default_interrupt_reason_provider=signal.poll)
    reviewer = SimpleNamespace(_default_interrupt_reason_provider=signal.poll)
    assert hold_engine(engineer)._hold_before_backend_failure_retry(0, retry_runner=reviewer) is None
    assert calls == [True]


def test_request_scope_wins_without_consuming_role_mailboxes():
    calls = []
    backend = SimpleNamespace(_default_interrupt_reason_provider=lambda: calls.append(True))
    with run_interrupt_scope(lambda: "operator abort requested: newer request"):
        reason = hold_engine(backend)._hold_before_backend_failure_retry(15, retry_runner=backend)
    assert reason == "operator abort requested: newer request"
    assert calls == []


def test_broken_reviewer_callback_does_not_hide_engineer_stop():
    def broken():
        raise OSError("The independent Reviewer control source could not be read")

    reviewer = SimpleNamespace(_default_interrupt_reason_provider=broken)
    engineer = SimpleNamespace(_default_interrupt_reason_provider=lambda: "daemon stop requested")
    assert hold_engine(engineer)._hold_before_backend_failure_retry(15, retry_runner=reviewer) == "daemon stop requested"
