import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from argus.advisor.config import AdvisorConfig
from argus.advisor.receipts import read_receipt, recent_receipts
from argus.advisor.service import AdvisorCallContext, AdvisorService, make_advisor_backend
from argus.core.models import RunnerResult


def fixture(tmp_path, *, config=None, action=None, emit=None, interrupt=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "evidence.txt").write_text("The focused test passed.")
    calls, backends, events = [], [], []

    class Backend:
        def set_usage_context(self, **kwargs):
            self.usage = kwargs

        def run_exec(self, **kwargs):
            calls.append(kwargs)
            if action:
                return action(kwargs)
            return RunnerResult(exit_code=0, agent_messages=["Check one remaining case in workspace:evidence.txt."],
                                usage_model="independent/expert", call_id="advisor-provider-call")

    def factory(*_args):
        backend = Backend()
        backends.append(backend)
        return backend

    context = AdvisorCallContext(
        tmp_path / "state", workspace, "reviewer", "parent-call", "mission-7", tmp_path / "global", interrupt,
    )
    service = AdvisorService(
        context, config=config or AdvisorConfig(enabled=True, backend="pi", model="independent/expert"),
        backend_factory=factory, redact=lambda text: text, emit=emit or events.append,
    )
    return service, calls, backends, events


def test_independent_call_binds_budget_evidence_and_audit_without_repeating(tmp_path):
    service, calls, backends, events = fixture(tmp_path)
    result = service.consult("What remains uncertain?", ["evidence.txt"], "tool-call-1")
    assert result["status"] == "completed"
    assert result["parent_call_id"] == "parent-call" and result["mission_id"] == "mission-7"
    assert result["call_id"] == "advisor-provider-call"
    options = calls[0]["options"]
    assert options.model == "independent/expert"
    assert options.disable_tools and options.force_safe_mode and not options.dangerous_yolo
    assert calls[0]["resume_thread_id"] is None
    assert backends[0].usage == {
        "project_root": service.context.project_root, "mission_id": "mission-7", "global_root": service.context.global_root,
    }
    assert service.consult("What remains uncertain?", ["evidence.txt"], "tool-call-1") == result
    with pytest.raises(ValueError, match="different question or evidence"):
        service.consult("Has the result been independently verified?", ["evidence.txt"], "tool-call-1")
    with pytest.raises(ValueError, match="different question or evidence"):
        service.consult("What remains uncertain?", [], "tool-call-1")
    assert len(calls) == 1
    assert [event["type"] for event in events] == ["advisor.consultation.requested", "advisor.consultation.completed"]
    assert read_receipt(service.context.project_root, result["consultation_id"]) == result
    assert recent_receipts(service.context.project_root, after_ts=result["created_at"])[0] == result


def test_distinct_requests_have_independent_runners_and_call_allowance(tmp_path):
    service, calls, backends, _events = fixture(tmp_path)
    assert service.consult("First question", [], "one")["status"] == "completed"
    assert service.consult("Second question", [], "two")["status"] == "completed"
    assert service.consult("Third question", [], "three")["status"] == "limit_reached"
    assert len(calls) == 2 and backends[0] is not backends[1]


@pytest.mark.parametrize("cause", ["tool_cancel", "parent_cancel", "turn_ended", "timeout"])
def test_cancellation_reaches_running_advisor(tmp_path, cause):
    started = threading.Event()
    parent_stop = threading.Event()

    def action(kwargs):
        started.set()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            reason = kwargs["options"].external_interrupt_reason_provider()
            if reason:
                return RunnerResult(exit_code=-1, fatal_error=reason)
            time.sleep(0.01)
        pytest.fail("advisor did not observe cancellation")

    config = AdvisorConfig(enabled=True, backend="pi", model="independent/expert", timeout_seconds=1)
    service, calls, _backends, _events = fixture(
        tmp_path, config=config, action=action,
        interrupt=lambda: "operator stopped the mission" if parent_stop.is_set() else None,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.consult, "Check this", [], "ongoing")
        assert started.wait(2)
        if cause == "tool_cancel":
            service.cancel("ongoing")
        elif cause == "parent_cancel":
            parent_stop.set()
        elif cause == "turn_ended":
            service.close()
        result = future.result(timeout=3)
    assert result["status"] == ("timed_out" if cause == "timeout" else "cancelled")
    assert len(calls) == 1


def test_concurrent_duplicate_gets_pending_receipt_without_second_provider_call(tmp_path):
    started, release = threading.Event(), threading.Event()

    def action(_kwargs):
        started.set()
        assert release.wait(3)
        return RunnerResult(exit_code=0, agent_messages=["advice"])

    service, calls, _backends, _events = fixture(tmp_path, action=action)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.consult, "Same question", [], "same-request")
        try:
            assert started.wait(2)
            assert service.consult("Same question", [], "same-request")["status"] == "requested"
        finally:
            release.set()
        assert future.result(2)["status"] == "completed"
    assert len(calls) == 1


def test_cancel_arriving_before_consult_never_starts_provider(tmp_path):
    service, calls, _backends, _events = fixture(tmp_path)
    service.cancel("not-yet-registered")
    result = service.consult("Question", [], "not-yet-registered")
    assert result["status"] == "cancelled" and calls == []


def test_failed_audit_before_call_cannot_spend_and_completed_receipt_survives_audit_failure(tmp_path):
    service, calls, _backends, _events = fixture(tmp_path, emit=lambda _event: False)
    assert service.consult("Question", [], "blocked")["status"] == "failed"
    assert calls == []
    service._emit = lambda event: event["type"].endswith("requested")
    result = service.consult("Next question", [], "completed")
    assert result["status"] == "completed" and result["audit_event_pending"]
    assert service.consult("Next question", [], "completed") == result
    assert len(calls) == 1


def test_reported_different_model_is_explicit_failure(tmp_path):
    service, _calls, _backends, _events = fixture(tmp_path, action=lambda _kwargs: RunnerResult(
        exit_code=0, agent_messages=["wrong model answered"], usage_model="main/worker",
    ))
    result = service.consult("Question", [])
    assert result["status"] == "model_mismatch" and result["reported_model"] == "main/worker"


def test_backend_factory_never_uses_main_model_or_falls_back_to_another_runner(monkeypatch):
    from argus.agent_cli import runner_backend
    from argus.trial import client

    monkeypatch.setattr(runner_backend, "resolve_runner_bin", lambda *_args: None)
    config = AdvisorConfig(enabled=True, backend="codex", model="explicit-advisor")
    with pytest.raises(ValueError, match="unavailable"):
        make_advisor_backend(config, lambda: None)
    monkeypatch.setattr(client, "trial_model_options", lambda *_args: ("main-model", "high"))
    with pytest.raises(ValueError, match="override"):
        make_advisor_backend(replace(config, backend="copilot"), lambda: None)
