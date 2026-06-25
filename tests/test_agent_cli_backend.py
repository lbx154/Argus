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

import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from argus_skill.adapters.agent_cli_backend import (
    AgentCliBackend,
    _sum_token_counts,
    build_agent_cli_backend_from_env,
)
from argus_skill.core.models import RunnerOptions


@dataclass
class ArgusRunnerOptions:
    model: str = "gpt-5.4-mini"
    reasoning_effort: str = "medium"
    dangerous_yolo: bool = False
    full_auto: bool = False
    skip_git_repo_check: bool = False
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
    exit_code: int = 0,
    agent_messages: list[str] | None = None,
    json_events: list[dict[str, Any]] | None = None,
    thread_id: str | None = "thr-abc123",
    fatal_error: str | None = None,
    stdout_lines: list[str] | None = None,
    stderr_lines: list[str] | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        command=["codex", "exec", "-"],
        exit_code=exit_code,
        thread_id=thread_id,
        agent_messages=list(agent_messages or []),
        json_events=list(json_events or []),
        stdout_lines=list(stdout_lines or []),
        stderr_lines=list(stderr_lines or []),
        turn_completed=exit_code == 0,
        turn_failed=exit_code != 0,
        fatal_error=fatal_error,
    )


def test_run_exec_translates_options_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        working_dir="/tmp/wd",
        extra_args=["-c", "config_profile=tb"],
        full_auto=True,
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
    assert forwarded.working_dir == "/tmp/wd"
    assert forwarded.extra_args == ["-c", "config_profile=tb"]
    assert forwarded.full_auto is True
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
    in_tok, cached_tok, out_tok = _sum_token_counts(None)
    assert (in_tok, cached_tok, out_tok) == (0, 0, 0)
    in_tok, cached_tok, out_tok = _sum_token_counts([])
    assert (in_tok, cached_tok, out_tok) == (0, 0, 0)


def test_token_count_extraction_picks_latest_nonzero():
    events = [
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 30},
        # a later event with zero tokens shouldn't overwrite the earlier non-zero
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 250, "cached_input_tokens": 25, "output_tokens": 80},
    ]
    in_tok, cached_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok) == (250, 25, 80)


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
    in_tok, cached_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok) == (150, 0, 40)


def test_token_count_extraction_handles_nested_content():
    events = [
        {
            "type": "msg",
            "content": {"input_tokens": 42, "cached_input_tokens": 5, "output_tokens": 7},
        }
    ]
    in_tok, cached_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok) == (42, 5, 7)


def test_token_count_extraction_handles_top_level_cached_tokens():
    events = [
        {
            "type": "token_count",
            "input_tokens": 17,
            "cached_input_tokens": 4,
            "output_tokens": 3,
        }
    ]
    in_tok, cached_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok) == (17, 4, 3)


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
    in_tok, cached_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, cached_tok, out_tok) == (12944, 1234, 75)


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
