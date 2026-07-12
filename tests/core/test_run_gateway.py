from __future__ import annotations

import ast
from pathlib import Path

import pytest

from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.core.run_gateway import RunExecGateway, RunExecRequest, run_exec


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


def test_application_code_has_no_direct_backend_run_exec_bypass() -> None:
    package = Path(__file__).parents[2] / "argus_skill"
    allowed = {
        package / "adapters" / "agent_cli_backend.py",
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
