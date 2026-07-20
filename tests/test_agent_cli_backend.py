"""Tests for ``argus_skill.adapters.agent_cli_backend``.

We do NOT spawn a real codex / claude CLI in CI. Instead we monkey-patch
the underlying ``AgentCliRunner.run_exec`` to return a synthetic
``AgentRunResult``, then verify our adapter:

  * Translates argus-skill ``RunnerOptions`` → ArgusBot ``RunnerOptions``
    correctly (model, reasoning_effort, working_dir, extra_args,
    full_auto, skip_git_repo_check, dangerous_yolo).
  * Translates ``AgentRunResult`` → argus-skill ``RunnerResult``
    correctly, including agent_messages, stdout/stderr lines, thread_id,
    fatal_error.
  * Sums token counts from the JSON event stream (last non-zero wins).
  * Catches subprocess failures (FileNotFoundError, generic exceptions)
    and surfaces them as a ``RunnerResult`` with ``fatal_error`` set.
  * ``build_agent_cli_backend_from_env`` honours env vars.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from argus_skill.adapters.agent_cli_backend import (
    AgentCliBackend,
    _sum_token_counts,
    build_agent_cli_backend_from_env,
)
from argus_skill.core.copilot_usage import CopilotCallUsage, CopilotModelUsage
from argus_skill.core.models import RunnerOptions


@dataclass
class ArgusRunnerOptions:
    model: str = "gpt-5.4-mini"
    reasoning_effort: str = "medium"
    dangerous_yolo: bool = False
    full_auto: bool = False
    skip_git_repo_check: bool = False
    sandbox_mode: str | None = None
    extra_args: list[str] | None = None
    working_dir: str | None = None
    output_schema_path: str | None = None
    external_interrupt_reason_provider: Any | None = None
    inactivity_callback: Any | None = None
    watchdog_soft_idle_seconds: int = 0
    watchdog_hard_idle_seconds: int = 0


@dataclass
class AgentRunResult:
    command: list[str]
    exit_code: int
    thread_id: str | None
    agent_messages: list[str]
    json_events: list[dict[str, Any]]
    stdout_lines: list[str]
    stderr_lines: list[str]
    turn_completed: bool
    turn_failed: bool
    fatal_error: str | None = None
    usage_model: str = ""


class AgentCliRunner:
    def __init__(
        self,
        *,
        agent_bin: str | None = None,
        backend: str = "codex",
        event_callback: Any | None = None,
        default_extra_args: list[str] | None = None,
        before_exec: Any | None = None,
    ) -> None:
        self.agent_bin = agent_bin
        self.backend = backend
        self.event_callback = event_callback
        self.default_extra_args = list(default_extra_args or [])
        self.before_exec = before_exec

    def run_exec(self, *, prompt, resume_thread_id, options, run_label):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fake_agent_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = ModuleType("agent_cli")
    setattr(pkg, "__path__", [])

    runner_mod = ModuleType("agent_cli.agent_cli_runner")
    runner_mod.__dict__["AgentCliRunner"] = AgentCliRunner
    runner_mod.__dict__["RunnerOptions"] = ArgusRunnerOptions

    backend_mod = ModuleType("agent_cli.runner_backend")
    backend_mod.__dict__["BACKEND_CLAUDE"] = "claude"
    backend_mod.__dict__["BACKEND_CODEX"] = "codex"
    backend_mod.__dict__["BACKEND_COPILOT"] = "copilot"
    backend_mod.__dict__["DEFAULT_RUNNER_BACKEND"] = "codex"

    def default_runner_bin() -> str | None:
        return "codex"

    def normalize_runner_backend(backend: str | None) -> str:
        return (backend or "codex").lower()

    backend_mod.__dict__["default_runner_bin"] = default_runner_bin
    backend_mod.__dict__["normalize_runner_backend"] = normalize_runner_backend

    models_mod = ModuleType("agent_cli.models")
    models_mod.__dict__["AgentRunResult"] = AgentRunResult

    setattr(pkg, "agent_cli_runner", runner_mod)
    setattr(pkg, "runner_backend", backend_mod)
    setattr(pkg, "models", models_mod)

    monkeypatch.setitem(sys.modules, "agent_cli", pkg)
    monkeypatch.setitem(sys.modules, "agent_cli.agent_cli_runner", runner_mod)
    monkeypatch.setitem(sys.modules, "agent_cli.runner_backend", backend_mod)
    monkeypatch.setitem(sys.modules, "agent_cli.models", models_mod)

    # ``_import_argusbot()`` now prefers the vendored copy at
    # ``argus_skill.agent_cli`` over the legacy top-level package.
    # Mirror the mock there so the option-translation contract test
    # keeps exercising the same surface the production code uses.
    monkeypatch.setitem(sys.modules, "argus_skill.agent_cli", pkg)
    monkeypatch.setitem(
        sys.modules, "argus_skill.agent_cli.agent_cli_runner", runner_mod,
    )
    monkeypatch.setitem(
        sys.modules, "argus_skill.agent_cli.runner_backend", backend_mod,
    )
    monkeypatch.setitem(
        sys.modules, "argus_skill.agent_cli.models", models_mod,
    )


def _make_argus_result(
    *,
    command: list[str] | None = None,
    exit_code: int = 0,
    agent_messages: list[str] | None = None,
    json_events: list[dict[str, Any]] | None = None,
    thread_id: str | None = "thr-abc123",
    fatal_error: str | None = None,
    stdout_lines: list[str] | None = None,
    stderr_lines: list[str] | None = None,
    usage_model: str = "",
) -> AgentRunResult:
    return AgentRunResult(
        command=list(command or ["codex", "exec", "-"]),
        exit_code=exit_code,
        thread_id=thread_id,
        agent_messages=list(agent_messages or []),
        json_events=list(json_events or []),
        stdout_lines=list(stdout_lines or []),
        stderr_lines=list(stderr_lines or []),
        turn_completed=exit_code == 0,
        turn_failed=exit_code != 0,
        fatal_error=fatal_error,
        usage_model=usage_model,
    )


def test_run_exec_translates_options_and_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=tmp_path / ".argus")
    captured: dict[str, Any] = {}

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        captured["prompt"] = prompt
        captured["resume_thread_id"] = resume_thread_id
        captured["options"] = options
        captured["run_label"] = run_label
        assert isinstance(options, ArgusRunnerOptions)
        return _make_argus_result(
            agent_messages=["hello world", "final answer"],
            json_events=[
                {
                    "type": "token_count",
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 50,
                },
                {
                    "type": "token_count",
                    "input_tokens": 250,
                    "cached_input_tokens": 25,
                    "output_tokens": 75,
                },
            ],
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    options = RunnerOptions(
        model="gpt-5.4-mini",
        reasoning_effort="high",
        working_dir=str(tmp_path),
        extra_args=["-c", "config_profile=tb"],
        full_auto=True,
        sandbox_mode="read-only",
        skip_git_repo_check=True,
        dangerous_yolo=False,
        output_schema_path="/tmp/schema.json",
    )
    result = backend.run_exec(
        prompt="say hi",
        options=options,
        run_label="engineer-r1",
        resume_thread_id="thr-prev",
    )

    # --- options were translated correctly
    forwarded = captured["options"]
    assert forwarded.model == "gpt-5.4-mini"
    assert forwarded.reasoning_effort == "high"
    assert forwarded.working_dir == str(tmp_path)
    assert forwarded.extra_args == ["-c", "config_profile=tb"]
    assert forwarded.full_auto is True
    assert forwarded.sandbox_mode == "read-only"
    assert forwarded.skip_git_repo_check is True
    assert forwarded.dangerous_yolo is False
    assert forwarded.output_schema_path == "/tmp/schema.json"
    assert captured["resume_thread_id"] == "thr-prev"
    assert captured["run_label"] == "engineer-r1"
    assert captured["prompt"] == "say hi"

    # --- result was translated correctly
    assert result.exit_code == 0
    assert result.agent_messages == ["hello world", "final answer"]
    assert result.last_agent_message == "final answer"
    assert result.thread_id == "thr-abc123"
    assert result.fatal_error is None
    # Token counts: latest non-zero wins.
    assert result.input_tokens == 250
    assert result.cached_input_tokens == 25
    assert result.output_tokens == 75
    usage_rows = [
        json.loads(line)
        for line in (tmp_path / ".argus" / "usage.jsonl").read_text().splitlines()
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0]["call_id"] == result.call_id
    assert result.call_id_log_correlated is True


def test_usage_context_keeps_explicit_global_budget_root(tmp_path: Path) -> None:
    backend = AgentCliBackend(backend="codex")
    project = tmp_path / "state" / "projects" / "s-test"
    global_root = tmp_path / "state"

    backend.set_usage_context(
        project_root=project,
        global_root=global_root,
        mission_id="mission-1",
    )

    assert backend._usage_context_snapshot() == (
        project,
        "mission-1",
        global_root,
    )


def test_run_exec_passes_global_budget_root_to_cost_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")
    project = tmp_path / "state" / "projects" / "s-test"
    global_root = tmp_path / "state"
    backend.set_usage_context(
        project_root=project,
        global_root=global_root,
        mission_id="mission-1",
    )
    captured: dict[str, Any] = {}

    def deny_after_capture(**kwargs):
        captured.update(kwargs)
        return None, "captured reservation"

    monkeypatch.setattr(
        "argus_skill.core.cost_control.cost_control_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.cost_control.reserve_call_budget",
        deny_after_capture,
    )

    result = backend.run_exec(
        prompt="test",
        options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="manager-frontdoor-classify",
    )

    assert result.exit_code == -1
    assert captured["project_root"] == project
    assert captured["global_root"] == global_root


def test_completed_run_exec_counts_after_mission_process_is_killed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(project / "events.jsonl"))
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-killed")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return _make_argus_result(
            json_events=[
                {
                    "type": "token_count",
                    "input_tokens": 1000,
                    "cached_input_tokens": 0,
                    "output_tokens": 100,
                }
            ],
            thread_id="kill-thread",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    result = backend.run_exec(
        prompt="complete one call",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )

    # No life.mission.completed event is written: this models SIGKILL after the
    # completed call returned. The daily aggregate still reads the durable call.
    from argus_skill.life.supervisor import global_daily_spend

    assert result.cost_usd == pytest.approx(0.008)
    assert global_daily_spend(global_root=root) == pytest.approx(result.cost_usd)


def test_run_exec_atomically_reserves_and_settles_call_cost(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1")
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-1")

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        lambda self, **kwargs: _make_argus_result(
            json_events=[{
                "type": "token_count",
                "input_tokens": 1_000,
                "output_tokens": 100,
            }],
            thread_id="cost-thread",
        ),
        raising=True,
    )

    result = backend.run_exec(
        prompt="priced call",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )

    assert result.cost_usd == pytest.approx(0.008)
    state = json.loads((root / "cost-control.json").read_text())
    assert state["reservations"] == []
    assert state["unresolved"] == []
    rows = [
        json.loads(line)
        for line in (project / "events.jsonl").read_text().splitlines()
    ]
    assert [row["type"] for row in rows] == [
        "budget.reservation.created",
        "agent.io.start",
        "agent.io.complete",
        "usage.recorded",
        "budget.reservation.settled",
    ]
    assert rows[0]["amount_usd"] == pytest.approx(1.0)
    assert rows[-1]["cost_usd"] == pytest.approx(0.008)
    metrics = [
        json.loads(line)
        for line in (root / "metrics.jsonl").read_text().splitlines()
    ]
    provider_metric = next(row for row in metrics if row["name"] == "provider.call")
    assert provider_metric["labels"]["status"] == "completed"
    assert provider_metric["fields"]["call_id"] == result.call_id


def test_single_call_overrun_is_recorded_in_usd(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0.01")
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-overrun")
    captured: dict[str, Any] = {}

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        captured["options"] = kwargs["options"]
        return _make_argus_result(
            json_events=[{
                "type": "token_count",
                "input_tokens": 0,
                "output_tokens": 1_000,
            }],
            thread_id="overrun-thread",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    result = backend.run_exec(
        prompt="expensive single response",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )

    assert result.cost_usd == pytest.approx(0.03)
    rows = [
        json.loads(line)
        for line in (project / "events.jsonl").read_text().splitlines()
    ]
    settled = next(row for row in rows if row["type"] == "budget.reservation.settled")
    assert settled["overrun_usd"] == pytest.approx(0.02)
    metrics = [
        json.loads(line)
        for line in (root / "metrics.jsonl").read_text().splitlines()
    ]
    provider_metric = next(row for row in metrics if row["name"] == "provider.call")
    assert provider_metric["fields"]["overrun_usd"] == pytest.approx(0.02)


def test_unpriced_call_blocks_next_provider_spawn(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-1")
    calls = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        calls.append(kwargs["run_label"])
        return _make_argus_result(
            json_events=[{
                "type": "token_count",
                "input_tokens": 100,
                "output_tokens": 20,
            }],
            thread_id="unknown-thread",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        fake_run_exec,
        raising=True,
    )

    first = backend.run_exec(
        prompt="unknown price",
        options=RunnerOptions(model="future-model"),
        run_label="engineer-r1",
    )
    second = backend.run_exec(
        prompt="must not spawn",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="reviewer",
    )

    assert first.pricing_status == "unpriced"
    assert first.cost_usd is None
    assert calls == ["engineer-r1"]
    assert "unresolved provider cost blocks new calls" in str(second.fatal_error)
    assert second.pricing_status == "not_billed"
    state = json.loads((root / "cost-control.json").read_text())
    assert [row["call_id"] for row in state["unresolved"]] == [first.call_id]


def test_missing_copilot_resume_target_does_not_poison_cost_control(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.capture_copilot_usage_cursor",
        lambda: None,
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.read_copilot_usage_since",
        lambda *args, **kwargs: None,
    )
    backend = AgentCliBackend(backend="copilot")
    backend.set_usage_context(project_root=project, mission_id="mission-1")
    resumes: list[str | None] = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        resume = kwargs["resume_thread_id"]
        resumes.append(resume)
        if resume:
            return _make_argus_result(
                exit_code=1,
                thread_id=resume,
                fatal_error=(
                    "Error: No session, task, or name matched 'stale-thread'."
                ),
            )
        return _make_argus_result(thread_id="fresh-thread")

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        fake_run_exec,
        raising=True,
    )

    stale = backend.run_exec(
        prompt="resume",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="manager",
        resume_thread_id="stale-thread",
    )
    fresh = backend.run_exec(
        prompt="fresh",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="manager",
    )

    assert resumes == ["stale-thread", None]
    assert stale.pricing_status == "not_billed"
    assert stale.cost_usd == 0.0
    assert fresh.exit_code == 0


def test_run_exec_writes_full_agent_io_log(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        assert self.event_callback is not None
        thread = threading.Thread(
            target=self.event_callback,
            args=("manager.stdout", '{"type":"agent_message","message":"thinking"}'),
        )
        thread.start()
        thread.join()
        self.event_callback("stderr", "tool stderr line")
        return _make_argus_result(
            command=["copilot", "-p", "<prompt>"],
            agent_messages=["final answer"],
            json_events=[{"type": "agent_message", "message": "thinking"}],
            stdout_lines=['{"type":"agent_message","message":"thinking"}'],
            stderr_lines=["tool stderr line"],
            thread_id="thread-1",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    backend.run_exec(
        prompt="full prompt text",
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="manager",
        resume_thread_id="old-thread",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    raw_rows = [
        json.loads(line)
        for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()
    ]
    assert all(row["event_schema_version"] == 1 for row in rows)
    assert all("event_validation" not in row for row in rows)
    assert [row["type"] for row in rows] == [
        "agent.io.start",
        "agent.io.complete",
        "usage.recorded",
    ]
    assert [row["type"] for row in raw_rows] == [
        "agent.io.stream",
        "agent.io.stream",
    ]
    assert [row["io_kind"] for row in rows[:-1]] == ["start", "complete"]
    assert [row["io_kind"] for row in raw_rows] == ["stream", "stream"]
    assert rows[0]["prompt"] == "full prompt text"
    assert rows[0]["run_label"] == "manager"
    assert [row["stream"] for row in raw_rows] == [
        "stdout",
        "stderr",
    ]
    assert raw_rows[0]["stream"] == "stdout"
    assert raw_rows[0]["model"] == "gpt-5.5"
    assert raw_rows[0]["line"].startswith('{"type"')
    assert raw_rows[1]["stream"] == "stderr"
    assert raw_rows[1]["model"] == "gpt-5.5"
    assert "agent_messages" not in rows[-2]
    assert "stdout_lines" not in rows[-2]
    assert "stderr_lines" not in rows[-2]
    assert "json_events" not in rows[-2]
    assert rows[-2]["agent_message_count"] == 1
    assert rows[-2]["stdout_line_count"] == 1
    assert rows[-2]["stderr_line_count"] == 1
    assert rows[-2]["json_event_count"] == 1
    assert rows[-2]["command"] == ["copilot", "-p", "<prompt>"]
    assert rows[-2]["thread_id"] == "thread-1"
    assert rows[-1]["schema_version"] == 2
    assert rows[-1]["thread_id"] == "thread-1"
    assert rows[-1]["started_at"] <= rows[-1]["completed_at"]
    assert rows[-1]["duration_ms"] >= 0
    assert rows[-1]["usage"]["models"] == []
    assert rows[-1]["pricing"]["status"] == rows[-1]["pricing_status"]
    assert rows[-1]["pricing"]["cost_basis"] == rows[-1]["cost_basis"]
    usage_rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0]["call_id"] == rows[-2]["call_id"]


def test_full_agent_io_batches_raw_stream_writes(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.adapters.agent_cli_backend as backend_module

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_BATCH_BYTES", "65536")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_FLUSH_INTERVAL_S", "60")
    batch_sizes: list[int] = []
    original_append = backend_module._jsonl_append_lines

    def recording_append(path, lines, lock):  # noqa: ANN001
        batch_sizes.append(len(lines))
        original_append(path, lines, lock)

    monkeypatch.setattr(backend_module, "_jsonl_append_lines", recording_append)
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        assert self.event_callback is not None
        for index in range(1_000):
            self.event_callback(
                "stdout",
                json.dumps({
                    "type": "assistant.tool_call_delta",
                    "data": {"index": index, "delta": "x" * 32},
                }),
            )
        return _make_argus_result(
            agent_messages=["done"],
            stdout_lines=["tail"],
            thread_id="batch-thread",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        fake_run_exec,
        raising=True,
    )
    backend.run_exec(
        prompt="batch",
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()
    ]
    assert sum(batch_sizes) == 1_000
    assert len(batch_sizes) < 10
    assert sum(row["type"] == "agent.io.stream" for row in rows) == 1_000
    control_rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert "json_events" not in next(
        row for row in control_rows if row["type"] == "agent.io.complete"
    )


def test_full_io_persists_prompt_once_not_as_user_message_echo(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_MODE", "full")
    backend = AgentCliBackend(backend="copilot")
    prompt = "large prompt body that must be stored exactly once"

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        assert self.event_callback is not None
        self.event_callback(
            "stdout",
            json.dumps({
                "type": "user.message",
                "data": {"content": kwargs["prompt"]},
            }),
        )
        self.event_callback(
            "stdout",
            json.dumps({
                "type": "assistant.message_delta",
                "data": {"deltaContent": "ok"},
            }),
        )
        return _make_argus_result(
            agent_messages=["ok"],
            thread_id="prompt-once",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        fake_run_exec,
        raising=True,
    )
    backend.run_exec(
        prompt=prompt,
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    raw_rows = [
        json.loads(line)
        for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()
    ]
    start = next(row for row in rows if row["type"] == "agent.io.start")
    streams = [row for row in raw_rows if row["type"] == "agent.io.stream"]
    assert start["prompt"] == prompt
    assert len(streams) == 1
    assert "assistant.message_delta" in streams[0]["line"]


def test_copilot_run_exec_uses_exact_session_store_tokens(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return _make_argus_result(
            agent_messages=["OK"],
            json_events=[{"type": "result", "usage": {"premiumRequests": 1.0}}],
            thread_id="session-1",
        )

    exact = CopilotCallUsage((CopilotModelUsage(
        row_id=1,
        session_id="session-1",
        turn_index=0,
        model="gpt-5.6-sol",
        input_tokens=25_819,
        output_tokens=8,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        total_nano_aiu=16_160_500_000,
        request_multiplier=1.0,
        created_at="2026-07-11T09:59:25.919Z",
    ),))
    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.read_copilot_usage_since",
        lambda cursor, session_id: exact,
    )

    result = backend.run_exec(
        prompt="reply",
        options=RunnerOptions(model="wrong-configured-model", working_dir=str(tmp_path)),
        run_label="simple-1",
    )
    assert result.input_tokens == 25_819
    assert result.output_tokens == 8
    assert result.usage_model == "gpt-5.6-sol"
    assert result.total_nano_aiu == 16_160_500_000
    assert result.cost_usd == pytest.approx(0.161605)
    usage = json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[0])
    assert usage["model"] == "gpt-5.6-sol"
    assert usage["cost_basis"] == "token"
    assert usage["premium_requests"] == 1.0
    assert usage["premium_request_cost_usd"] == pytest.approx(0.04)
    assert usage["model_usage"][0]["usage_event_id"] == 1
    assert usage["model_usage"][0]["session_id"] == "session-1"
    event = json.loads(log_path.read_text().splitlines()[-1])
    assert event["type"] == "usage.recorded"
    assert event["schema_version"] == 2
    assert event["thread_id"] == "session-1"
    assert event["usage"]["models"][0]["model"] == "gpt-5.6-sol"
    assert event["usage"]["models"][0]["input_tokens"] == 25_819
    assert event["usage"]["models"][0]["usage_event_id"] == 1
    assert event["usage"]["models"][0]["session_id"] == "session-1"
    assert event["usage"]["models"][0]["cost_usd"] == pytest.approx(0.161605)
    assert event["pricing"]["cost_usd"] == pytest.approx(0.161605)


def test_copilot_resumed_premium_counter_without_baseline_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(tmp_path / "events.jsonl"))
    backend = AgentCliBackend(backend="copilot")
    raw_totals = iter((15.0, 22.5))

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return _make_argus_result(
            agent_messages=["OK"],
            json_events=[{
                "type": "result",
                "usage": {"premiumRequests": next(raw_totals)},
            }],
            thread_id="resumed-session",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.read_copilot_usage_since",
        lambda cursor, session_id: None,
    )
    options = RunnerOptions(model="gpt-5.6-sol", working_dir=str(tmp_path))

    first = backend.run_exec(
        prompt="first after restart",
        options=options,
        run_label="manager-frontdoor-classify",
        resume_thread_id="resumed-session",
    )
    second = backend.run_exec(
        prompt="second after restart",
        options=options,
        run_label="planner",
        resume_thread_id="resumed-session",
    )

    assert first.premium_requests == 0.0
    assert first.premium_requests_present is False
    assert first.pricing_status == "partial"
    assert first.cost_usd is None
    assert second.premium_requests == pytest.approx(7.5)
    assert second.premium_requests_present is True
    assert second.pricing_status == "priced"
    assert second.cost_usd == pytest.approx(0.30)

    rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    assert rows[0]["premium_requests"] is None
    assert rows[0]["pricing_status"] == "partial"
    assert rows[1]["premium_requests"] == pytest.approx(7.5)
    assert rows[1]["cost_usd"] == pytest.approx(0.30)
    complete_rows = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if '"type":"agent.io.complete"' in line
    ]
    assert complete_rows[0]["premium_requests"] is None
    assert complete_rows[0]["premium_requests_present"] is False
    assert complete_rows[1]["premium_requests"] == pytest.approx(7.5)
    assert complete_rows[1]["premium_requests_present"] is True


def test_copilot_acp_session_model_overrides_mislabeled_usage_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="copilot")

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        lambda self, **kwargs: _make_argus_result(
            agent_messages=["OK"],
            thread_id="session-mini",
            usage_model="gpt-5.4-mini",
        ),
        raising=True,
    )
    mislabeled = CopilotCallUsage((CopilotModelUsage(
        row_id=2,
        session_id="session-mini",
        turn_index=0,
        model="gpt-5.6-sol",
        input_tokens=100,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        total_nano_aiu=10,
        request_multiplier=0.33,
        created_at="2026-07-15T10:00:00Z",
    ),))
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.read_copilot_usage_since",
        lambda cursor, session_id: mislabeled,
    )

    result = backend.run_exec(
        prompt="classify",
        options=RunnerOptions(model="gpt-5.4-mini", working_dir=str(tmp_path)),
        run_label="manager-frontdoor-classify",
    )

    assert result.usage_model == "gpt-5.4-mini"
    assert result.model_usage[0]["model"] == "gpt-5.4-mini"


def test_usage_context_prefers_canonical_project_event_log(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    legacy_path = project / ".argus" / "events.jsonl"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps({"type": "agent.io.start", "call_id": "old-call"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(legacy_path))
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-1")

    resolved = backend._agent_io_log_path(
        RunnerOptions(working_dir=str(tmp_path / "worktree"))
    )

    assert resolved == project / "events.jsonl"
    migrated = [
        json.loads(line)
        for line in resolved.read_text(encoding="utf-8").splitlines()
    ]
    assert migrated == [{"type": "agent.io.start", "call_id": "old-call"}]
    assert (project / "events.migration-v2.json").exists()


def test_default_agent_io_is_bounded_and_drops_duplicate_stream(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_MODE", "compact")
    live: list[tuple[str, str]] = []
    backend = AgentCliBackend(
        backend="copilot",
        event_callback=lambda stream, line: live.append((stream, line)),
    )

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        assert self.event_callback is not None
        self.event_callback(
            "stdout",
            '{"type":"assistant.tool_call_delta","data":{"delta":"noise"}}',
        )
        self.event_callback("stdout", '{"type":"assistant.message_delta","data":{"deltaContent":"huge"}}')
        return _make_argus_result(
            command=["copilot", "-p", "HUGE PROMPT"],
            agent_messages=["result"],
            json_events=[{"large": "payload"}],
            stdout_lines=["huge stream payload"],
            stderr_lines=[],
            thread_id="compact-thread",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    backend.run_exec(
        prompt="private compaction prompt" * 100,
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [row["type"] for row in rows] == [
        "agent.io.start",
        "agent.io.complete",
        "usage.recorded",
    ]
    assert "prompt" not in rows[0] and rows[0]["prompt_chars"] > 100
    assert len(rows[0]["prompt_sha256"]) == 64
    assert rows[1]["command"] == ["copilot", "-p", "<prompt>"]
    assert "agent_messages" not in rows[1]
    assert "stdout_lines" not in rows[1]
    assert "stderr_lines" not in rows[1]
    assert "json_events" not in rows[1]
    assert rows[1]["stdout_line_count"] == 1
    assert rows[1]["json_event_count"] == 1
    assert rows[1]["agent_message_count"] == 1
    assert rows[1]["agent_message_chars"] == len("result")
    assert len(rows[1]["last_agent_message_sha256"]) == 64
    assert len(live) == 1
    assert "assistant.message_delta" in live[0][1]


def test_codex_quota_events_and_daily_denial(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "1")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    backend = AgentCliBackend(backend="codex")
    calls = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        calls.append(kwargs["run_label"])
        return _make_argus_result(agent_messages=["ok"], thread_id="codex-thread")

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    first = backend.run_exec(
        prompt="first",
        options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )
    second = backend.run_exec(
        prompt="second",
        options=RunnerOptions(working_dir=str(tmp_path)),
        run_label="reviewer",
    )

    assert first.fatal_error is None
    assert "daily call cap 1 reached" in str(second.fatal_error)
    assert calls == ["engineer-r1"]
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [row["type"] for row in rows] == [
        "provider.request.started",
        "agent.io.start",
        "provider.request.completed",
        "agent.io.complete",
        "usage.recorded",
        "provider.request.denied",
        "usage.recorded",
    ]
    assert rows[0]["daily_calls"] == 1
    assert rows[0]["daily_cap"] == 1
    usage_rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    assert len(usage_rows) == 2
    # The engineer-r1 call pins no model; codex echoes none either. It used to
    # record an empty model -> "unpriced". Since the empty-model pricing fix it
    # is attributed to the configured default model, so with no token counts in
    # this synthetic result it is now "partial" (price known, tokens missing)
    # rather than "unpriced".
    assert {row["pricing_status"] for row in usage_rows} == {
        "partial",
        "not_billed",
    }


def test_run_exec_normalizes_recoverable_reconnect_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,  # noqa: ARG001
        resume_thread_id: Any,  # noqa: ARG001
        options: Any,  # noqa: ARG001
        run_label: str,  # noqa: ARG001
    ) -> AgentRunResult:
        return _make_argus_result(
            agent_messages=["continued after reconnect"],
            fatal_error=(
                "Reconnecting... 1/100 "
                "(stream disconnected before completion: response.failed event received)"
            ),
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    result = backend.run_exec(
        prompt="demo",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )

    assert result.last_agent_message == "continued after reconnect"
    assert result.fatal_error is None


def test_copilot_policy_denial_with_exit_zero_sets_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_SLOT_WAIT_S", "0")
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return AgentRunResult(
            command=["copilot"],
            exit_code=0,
            thread_id=None,
            agent_messages=[],
            json_events=[],
            stdout_lines=[],
            stderr_lines=["Your Copilot subscription does not include this feature"],
            turn_completed=False,
            turn_failed=True,
            fatal_error="Error: Access denied by policy settings",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )
    result = backend.run_exec(
        prompt="x",
        options=RunnerOptions(),
        run_label="reviewer",
    )

    assert result.fatal_error == "Error: Access denied by policy settings"
    assert backend._auth_failure_detected is True
    from argus_skill.core.copilot_guard import copilot_guard_snapshot

    assert copilot_guard_snapshot()["blocked_until"] > 0


def test_run_exec_normalizes_high_attempt_reconnect_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,  # noqa: ARG001
        resume_thread_id: Any,  # noqa: ARG001
        options: Any,  # noqa: ARG001
        run_label: str,  # noqa: ARG001
    ) -> AgentRunResult:
        return _make_argus_result(
            agent_messages=["continued after high-attempt reconnect"],
            fatal_error=(
                "Reconnecting... 100/100 "
                "(stream disconnected before completion: response.failed event received)"
            ),
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    result = backend.run_exec(
        prompt="demo",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )

    assert result.last_agent_message == "continued after high-attempt reconnect"
    assert result.fatal_error is None


def test_run_exec_handles_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = AgentCliBackend(backend="codex")

    def boom(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> None:
        raise FileNotFoundError("codex: not found")

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", boom, raising=True
    )

    result = backend.run_exec(
        prompt="anything",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )
    assert result.exit_code == 127
    assert result.fatal_error is not None
    assert "not found" in result.fatal_error
    assert result.agent_messages == []


def test_run_exec_handles_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")

    def boom(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> None:
        raise RuntimeError("subprocess died")

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", boom, raising=True
    )

    result = backend.run_exec(
        prompt="anything",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )
    assert result.exit_code == -1
    assert result.fatal_error is not None
    assert "RuntimeError" in result.fatal_error


def test_token_count_extraction_handles_missing_events():
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(None)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (0, 0, 0, 0)
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts([])
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (0, 0, 0, 0)


def test_token_count_extraction_picks_latest_nonzero():
    events = [
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 30},
        # a later event with zero tokens shouldn't overwrite the earlier non-zero
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 250, "cached_input_tokens": 25, "output_tokens": 80},
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (250, 25, 80, 0)


def test_token_count_extraction_uses_final_usage_tuple_even_with_zero_cached():
    events = [
        {
            "type": "token_count",
            "input_tokens": 100,
            "cached_input_tokens": 10,
            "output_tokens": 30,
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 150,
                "cached_input_tokens": 0,
                "output_tokens": 40,
            },
        },
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (150, 0, 40, 0)


def test_token_count_extraction_handles_nested_content():
    events = [
        {
            "type": "msg",
            "content": {"input_tokens": 42, "cached_input_tokens": 5, "output_tokens": 7},
        }
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (42, 5, 7, 0)


def test_token_count_extraction_handles_top_level_cached_tokens():
    events = [
        {
            "type": "token_count",
            "input_tokens": 17,
            "cached_input_tokens": 4,
            "output_tokens": 3,
        }
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (17, 4, 3, 0)


def test_token_count_extraction_reads_codex_0_121_usage_field():
    """codex-cli >=0.121 emits usage on turn.completed.

    Regression test for the $0.0000 cost bug: previously _sum_token_counts
    only inspected top-level / nested-content fields, so the usage payload
    on turn.completed was silently ignored.
    """
    events = [
        {"type": "thread.started", "thread_id": "x"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12944, "cached_input_tokens": 1234, "output_tokens": 75},
        },
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (12944, 1234, 75, 0)


def test_token_count_extraction_reads_reasoning_tokens_from_turn_completed_usage() -> None:
    events = [
        {"type": "thread.started", "thread_id": "x"},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 954691,
                "cached_input_tokens": 846976,
                "output_tokens": 11399,
                "reasoning_output_tokens": 4459,
            },
        },
    ]
    in_tok, cached_tok, out_tok, reasoning_out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok, reasoning_out_tok) == (
        954691,
        846976,
        11399,
        4459,
    )


def test_usage_delta_for_thread_decumulates_reasoning_output_tokens() -> None:
    backend = AgentCliBackend(backend="codex")
    assert backend._usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(100, 10, 20, 7),
    ) == (100, 10, 20, 7)
    assert backend._usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(160, 30, 45, 19),
    ) == (60, 20, 25, 12)
    assert backend._usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(20, 5, 6, 2),
    ) == (20, 5, 6, 2)


def test_run_exec_forwards_watchdog_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watchdog hooks on argus-skill RunnerOptions must reach ArgusBot.

    A MissionDaemon-driven supervisor passes ``external_interrupt_reason_provider``
    so it can interrupt a long-running engineer turn promptly when an
    operator sends ``/inject`` or ``/stop``. If the adapter drops these
    fields, /inject becomes ineffective during a round.
    """
    backend = AgentCliBackend(backend="codex")
    captured: dict[str, Any] = {}

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        captured["options"] = options
        return _make_argus_result(agent_messages=["ok"])

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    interrupt_calls: list[None] = []

    def interrupt_provider() -> str | None:
        interrupt_calls.append(None)
        return None

    def inactivity_callback(snapshot: Any) -> str | None:  # noqa: ARG001
        return None

    options = RunnerOptions(
        model="gpt-5.4-mini",
        external_interrupt_reason_provider=interrupt_provider,
        inactivity_callback=inactivity_callback,
        watchdog_soft_idle_seconds=120,
        watchdog_hard_idle_seconds=600,
    )
    backend.run_exec(prompt="x", options=options, run_label="main")

    forwarded = captured["options"]
    assert forwarded.external_interrupt_reason_provider is interrupt_provider
    assert forwarded.inactivity_callback is inactivity_callback
    assert forwarded.watchdog_soft_idle_seconds == 120
    assert forwarded.watchdog_hard_idle_seconds == 600


def test_consumed_interrupt_returns_canonical_result_without_starting_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "0")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    backend = AgentCliBackend(backend="copilot")
    provider_calls = 0

    def one_shot_interrupt() -> str | None:
        nonlocal provider_calls
        provider_calls += 1
        return "operator abort requested: stop now" if provider_calls == 1 else None

    monkeypatch.setattr(
        backend._argus_runner.__class__,
        "run_exec",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not start after interrupt is consumed")
        ),
        raising=True,
    )

    result = backend.run_exec(
        prompt="x",
        options=RunnerOptions(
            model="gpt-5.6-sol",
            external_interrupt_reason_provider=one_shot_interrupt,
        ),
        run_label="engineer-r1",
    )

    assert provider_calls == 1
    assert result.exit_code == -1
    assert result.fatal_error == (
        "External interrupt: operator abort requested: stop now"
    )


def test_run_exec_applies_default_watchdog_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_interrupt = lambda: None
    backend = AgentCliBackend(
        backend="codex",
        default_interrupt_reason_provider=default_interrupt,
        default_watchdog_soft_idle_seconds=300,
        default_watchdog_hard_idle_seconds=1800,
    )
    captured: dict[str, Any] = {}

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        captured["options"] = options
        return _make_argus_result(agent_messages=["ok"])

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    backend.run_exec(
        prompt="x",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="main",
    )

    forwarded = captured["options"]
    assert forwarded.external_interrupt_reason_provider is default_interrupt
    assert forwarded.watchdog_soft_idle_seconds == 300
    assert forwarded.watchdog_hard_idle_seconds == 1800


def test_run_exec_composes_explicit_watchdog_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def default_interrupt() -> str | None:
        calls.append("default")
        return None

    def explicit_interrupt() -> str | None:
        calls.append("explicit")
        return "stale"

    backend = AgentCliBackend(
        backend="codex",
        default_interrupt_reason_provider=default_interrupt,
        default_watchdog_soft_idle_seconds=300,
        default_watchdog_hard_idle_seconds=1800,
    )
    captured: dict[str, Any] = {}

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        captured["options"] = options
        return _make_argus_result(agent_messages=["ok"])

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    backend.run_exec(
        prompt="x",
        options=RunnerOptions(
            model="gpt-5.4-mini",
            external_interrupt_reason_provider=explicit_interrupt,
            watchdog_soft_idle_seconds=10,
            watchdog_hard_idle_seconds=20,
        ),
        run_label="main",
    )

    forwarded = captured["options"]
    assert forwarded.external_interrupt_reason_provider is not explicit_interrupt
    assert forwarded.external_interrupt_reason_provider() == "stale"
    assert calls == ["default", "explicit"]
    assert forwarded.watchdog_soft_idle_seconds == 10
    assert forwarded.watchdog_hard_idle_seconds == 20


def test_run_exec_reports_delta_for_resumed_cumulative_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")
    raw_usages = [
        {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 100},
        {"input_tokens": 1250, "cached_input_tokens": 500, "output_tokens": 130},
    ]

    def fake_run_exec(
        self: Any,
        *,
        prompt: Any,
        resume_thread_id: Any,
        options: Any,
        run_label: str,
    ) -> AgentRunResult:
        usage = raw_usages.pop(0)
        return _make_argus_result(
            thread_id="thr-cumulative",
            json_events=[{"type": "turn.completed", "usage": usage}],
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True
    )

    first = backend.run_exec(
        prompt="first",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )
    second = backend.run_exec(
        prompt="second",
        options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r2",
        resume_thread_id="thr-cumulative",
    )

    assert (first.input_tokens, first.cached_input_tokens, first.output_tokens) == (
        1000,
        400,
        100,
    )
    assert (second.input_tokens, second.cached_input_tokens, second.output_tokens) == (
        250,
        100,
        30,
    )


def test_run_exec_default_watchdog_options_are_inert():
    """When the caller doesn't supply watchdog hooks the translated
    ArgusBot options must still be valid (None providers + 0 thresholds).
    """
    options = RunnerOptions(model="gpt-5.4-mini")
    assert options.external_interrupt_reason_provider is None
    assert options.inactivity_callback is None
    assert options.watchdog_soft_idle_seconds == 0
    assert options.watchdog_hard_idle_seconds == 0


def test_build_agent_cli_backend_from_env_uses_env(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_EXTRA_ARGS", '-c "model_profile=fast"')
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", "120")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "900")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)

    backend = build_agent_cli_backend_from_env()
    inner = backend._argus_runner
    # ArgusBot stores backend on the inner runner.
    assert inner.backend == "claude"
    assert inner.default_extra_args == ["-c", "model_profile=fast"]
    assert backend._default_watchdog_soft_idle_seconds == 120
    assert backend._default_watchdog_hard_idle_seconds == 900


def test_build_agent_cli_backend_from_env_strips_legacy_auto_max_profile(
    monkeypatch,
):
    monkeypatch.setenv(
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
        '-c "profile = \\"auto-max\\"" --trace',
    )
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    backend = build_agent_cli_backend_from_env()
    assert backend._argus_runner.default_extra_args == ["--trace"]


def test_build_agent_cli_backend_from_env_defaults(monkeypatch):
    for name in (
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_RUNNER_BIN",
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
        "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS",
        "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    backend = build_agent_cli_backend_from_env()
    # ArgusBot's default is codex.
    assert backend._argus_runner.backend == "codex"
    assert backend._argus_runner.default_extra_args == []
    assert backend._default_watchdog_soft_idle_seconds == 0
    assert backend._default_watchdog_hard_idle_seconds == 3600
