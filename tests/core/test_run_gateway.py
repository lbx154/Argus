from __future__ import annotations

import ast
from pathlib import Path

import pytest

from argus.core.models import RunnerOptions, RunnerResult
from argus.core.run_gateway import (
    RunExecGateway,
    RunExecRequest,
    run_exec,
    run_interrupt_scope,
)


class _Backend:
    def __init__(self, result: RunnerResult | None = None) -> None:
        self.result = result or RunnerResult(exit_code=0, agent_messages=["ok"])
        self.calls = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_gateway_forwards_one_normalized_request_and_adds_identity() -> None:
    backend = _Backend()
    options = RunnerOptions(model="gpt-5.6-sol")

    result = run_exec(
        backend,
        prompt="hello",
        options=options,
        run_label="manager",
        resume_thread_id="thread-old",
    )

    assert backend.calls == [{
        "prompt": "hello",
        "options": options,
        "run_label": "manager",
        "resume_thread_id": "thread-old",
    }]
    assert result.call_id.startswith("gateway-")
    assert result.thread_id == "thread-old"
    assert result.started_at > 0
    assert result.completed_at >= result.started_at
    assert result.duration_ms >= 0


def test_gateway_preserves_adapter_owned_identity_and_timing() -> None:
    existing = RunnerResult(
        exit_code=0,
        call_id="adapter-call",
        thread_id="adapter-thread",
        started_at=10.0,
        completed_at=12.0,
        duration_ms=2_000,
    )
    result = RunExecGateway(_Backend(existing)).execute(
        RunExecRequest(
            prompt="hello",
            options=RunnerOptions(),
            run_label="engineer-r1",
            resume_thread_id="old-thread",
        )
    )
    assert result.call_id == "adapter-call"
    assert result.thread_id == "adapter-thread"
    assert result.started_at == 10.0
    assert result.completed_at == 12.0
    assert result.duration_ms == 2_000


def test_gateway_preserves_omitted_vs_explicit_fresh_resume_keyword() -> None:
    backend = _Backend()
    run_exec(
        backend,
        prompt="omitted",
        options=None,
        run_label="test",
    )
    run_exec(
        backend,
        prompt="explicit fresh",
        options=None,
        run_label="test",
        resume_thread_id=None,
    )
    assert "resume_thread_id" not in backend.calls[0]
    assert backend.calls[1]["resume_thread_id"] is None


def test_gateway_does_not_hide_backend_exceptions() -> None:
    class Broken:
        def run_exec(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("backend exploded")

    with pytest.raises(RuntimeError, match="backend exploded"):
        run_exec(
            Broken(),
            prompt="hello",
            options=RunnerOptions(),
            run_label="test",
        )


def test_scoped_interrupt_preserves_options_and_existing_provider():
    backend = _Backend()
    original = lambda: "budget stop"
    options = RunnerOptions(external_interrupt_reason_provider=original)
    stopped = []
    with run_interrupt_scope(lambda: "operator stop" if stopped else None):
        run_exec(backend, prompt="scoped", options=options, run_label="manager")
        scoped = backend.calls[-1]["options"]
        assert scoped is not options
        assert scoped.external_interrupt_reason_provider() == "budget stop"
        stopped.append(True)
        assert scoped.external_interrupt_reason_provider() == "operator stop"
    assert options.external_interrupt_reason_provider is original
    run_exec(backend, prompt="unrelated", options=None, run_label="engineer")
    assert backend.calls[-1]["options"] is None


def test_opt_in_reason_retention_survives_preparation_and_resets_after_scope():
    from argus.core.run_gateway import current_run_interrupt_reason

    backend = _Backend()
    reasons = ["operator abort requested: original one-shot reason"]
    with run_interrupt_scope(lambda: reasons.pop() if reasons else None):
        with run_interrupt_scope(lambda: None, retain_first_reason=True):
            # A preparatory state read polls first, before the provider call.
            assert current_run_interrupt_reason() == "operator abort requested: original one-shot reason"
            stopped = run_exec(backend, prompt="must not run", run_label="engineer")
            assert stopped.exit_code == 130
            assert "original one-shot reason" in stopped.fatal_error
            assert backend.calls == []
        assert current_run_interrupt_reason() is None
    run_exec(backend, prompt="unrelated later call", run_label="engineer")
    assert len(backend.calls) == 1 and backend.calls[0]["options"] is None


@pytest.mark.parametrize("late_reason", ["late operator abort", None])
def test_retained_reason_survives_concurrent_slow_poll(late_reason):
    from contextvars import copy_context
    from threading import Event, Thread, current_thread

    from argus.core.run_gateway import current_run_interrupt_reason

    main_thread = current_thread()
    entered, release = Event(), Event()
    observed = []

    def provider():
        if current_thread() is main_thread:
            return "first stop"
        entered.set()
        assert release.wait(3)
        return late_reason

    with run_interrupt_scope(provider, retain_first_reason=True):
        context = copy_context()
        worker = Thread(target=lambda: observed.append(context.run(current_run_interrupt_reason)), daemon=True)
        worker.start()
        try:
            assert entered.wait(3)
            # The slow poll must not block the current caller's stop, nor
            # overwrite or ignore it when the earlier poll eventually returns.
            assert current_run_interrupt_reason() == "first stop"
        finally:
            release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert observed == ["first stop"]
        assert current_run_interrupt_reason() == "first stop"


def test_application_code_has_no_direct_backend_run_exec_bypass() -> None:
    package = Path(__file__).parents[2] / "argus"
    allowed = {
        package / "adapters" / "agent_cli_backend" / "_exec.py",
        package / "adapters" / "agent_cli_backend" / "_exec_spawn.py",
        package / "core" / "run_gateway.py",
    }
    violations = []
    for path in package.rglob("*.py"):
        if path in allowed or "agent_cli" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_exec"
            ):
                violations.append(f"{path.relative_to(package)}:{node.lineno}")
    assert violations == []
