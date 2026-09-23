"""Tests for ``argus.adapters.agent_cli_backend``.

We do NOT spawn a real codex / claude CLI in CI. Instead we monkey-patch
the underlying ``AgentCliRunner.run_exec`` to return a synthetic
``AgentRunResult``, then verify our adapter:

  * Translates argus ``RunnerOptions`` → the bundled runner's own
    ``RunnerOptions`` correctly (model, reasoning_effort, working_dir,
    extra_args, full_auto, skip_git_repo_check, dangerous_yolo).
  * Translates ``AgentRunResult`` → argus ``RunnerResult``
    correctly, including agent_messages, stdout/stderr lines, thread_id,
    fatal_error.
  * Sums token counts from the JSON event stream (last non-zero wins).
  * Catches subprocess failures (FileNotFoundError, generic exceptions)
    and surfaces them as a ``RunnerResult`` with ``fatal_error`` set.
  * ``build_agent_cli_backend_from_env`` honours env vars.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from argus.adapters.agent_cli_backend import (
    AgentCliBackend,
    build_agent_cli_backend_from_env,
)
from argus.adapters.agent_cli_backend._core import _RepeatedToolCallGuard
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as CliRunnerOptions
from argus.agent_cli.models import AgentRunResult
from argus.core.models import RunnerOptions
from argus.core.token_usage import extract_token_usage, sum_token_counts
from argus.provider_integrations.authorization_retry import (
    BackendLoginRequired,
)
from argus.provider_integrations.copilot_usage import (
    CopilotCallUsage,
    CopilotModelUsage,
)


@pytest.mark.parametrize("dialect", ["claude", "cursor"])
def test_repeated_tool_guard_only_interrupts_consecutive_identical_calls(dialect) -> None:
    guard = _RepeatedToolCallGuard(limit=3)

    def tool(call_id: str, path: str) -> None:
        payload = {"name": "Read", "input": {"file_path": path}}
        event = (
            {"type": "tool_call", "subtype": "started", "tool_call": payload}
            if dialect == "cursor" else {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": call_id, **payload}]},
            }
        )
        guard.observe("stdout", json.dumps(event))

    def result(call_id: str, *, failed: bool) -> None:
        guard.observe(
            "stdout",
            json.dumps({
                "type": "user",
                "message": {
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "is_error": failed,
                    }]
                },
            }),
        )

    tool("one", "/missing-a")
    result("one", failed=True)
    tool("two", "/missing-b")
    result("two", failed=True)
    tool("three", "/missing-a")
    result("three", failed=True)
    assert guard.interrupt_reason() == ""

    for call_id in ("four", "five"):
        tool(call_id, "/missing-a")
        result(call_id, failed=True)

    assert "requested 3 consecutive times" in guard.interrupt_reason()

    guard.reset()
    tool("six", "/missing-a")
    result("six", failed=False)
    assert guard.interrupt_reason() == ""


def _make_cli_result(
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


@pytest.fixture
def cli_call(monkeypatch):
    call = Mock(return_value=_make_cli_result(agent_messages=["ok"]))
    monkeypatch.setattr(AgentCliRunner, "run_exec", call)
    return call


def _configure_relay_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    *,
    environment_token: str | None = None,
) -> Path:
    from argus.provider_integrations import authorization_retry

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model_provider = "copilot_relay"\n'
        '[model_providers.copilot_relay]\n'
        'base_url = "http://127.0.0.1:41419/v1"\n'
        'env_key = "COPILOT_RELAY_TOKEN"\n',
        encoding="utf-8",
    )
    relay_dir = tmp_path / ".config" / "copilot-codex-relay"
    relay_dir.mkdir(parents=True)
    credential_path = relay_dir / "env"
    credential_path.write_text(
        f"COPILOT_RELAY_TOKEN={token}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        authorization_retry.Path,
        "home",
        classmethod(lambda cls: tmp_path),
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv(
        "COPILOT_RELAY_TOKEN",
        token if environment_token is None else environment_token,
    )
    return credential_path


def test_concurrent_401s_coordinate_one_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_path = _configure_relay_credential(
        tmp_path,
        monkeypatch,
        "fresh-relay-token",
        environment_token="rejected-relay-token",
    )
    backends = [AgentCliBackend(backend="codex") for _ in range(2)]
    barrier = threading.Barrier(2)
    state_lock = threading.Lock()
    calls_by_runner: dict[int, int] = {}
    replay_tokens: list[str] = []
    endpoint_expired = True
    refreshes = 0

    def fake_run_exec(self: Any, **_kwargs: Any) -> AgentRunResult:
        nonlocal endpoint_expired, refreshes
        with state_lock:
            runner_id = id(self)
            calls_by_runner[runner_id] = calls_by_runner.get(runner_id, 0) + 1
            call_number = calls_by_runner[runner_id]
        if call_number == 1:
            barrier.wait(timeout=5)
            return _make_cli_result(
                exit_code=1,
                fatal_error="401 Missing bearer",
                stderr_lines=["401 Missing bearer"],
            )
        with state_lock:
            needs_refresh = endpoint_expired
            replay_tokens.append(os.environ["COPILOT_RELAY_TOKEN"])
            if needs_refresh:
                refreshes += 1
        if needs_refresh:
            time.sleep(0.05)
            credential_path.write_text(
                "COPILOT_RELAY_TOKEN=newer-relay-token\n",
                encoding="utf-8",
            )
            with state_lock:
                endpoint_expired = False
        return _make_cli_result(agent_messages=["ok"])

    monkeypatch.setattr(AgentCliRunner, "run_exec", fake_run_exec, raising=True)
    options = RunnerOptions(skip_git_repo_check=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                backend.run_exec,
                prompt="answer",
                options=options,
                run_label="manager-pending-answer",
            )
            for backend in backends
        ]
        results = [future.result(timeout=5) for future in futures]

    assert [result.last_agent_message for result in results] == ["ok", "ok"]
    assert sorted(calls_by_runner.values()) == [2, 2]
    assert replay_tokens == ["fresh-relay-token", "newer-relay-token"]
    assert refreshes == 1


def test_second_401_requires_login_without_third_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_relay_credential(
        tmp_path,
        monkeypatch,
        "fresh-relay-token",
        environment_token="rejected-relay-token",
    )
    backend = AgentCliBackend(backend="codex")
    calls = 0

    def fake_run_exec(self: Any, **_kwargs: Any) -> AgentRunResult:
        nonlocal calls
        calls += 1
        return _make_cli_result(
            exit_code=1,
            fatal_error="401 Missing bearer",
            stderr_lines=["401 Missing bearer"],
        )

    monkeypatch.setattr(AgentCliRunner, "run_exec", fake_run_exec, raising=True)

    with pytest.raises(BackendLoginRequired) as caught:
        backend.run_exec(
            prompt="answer",
            options=RunnerOptions(skip_git_repo_check=True),
            run_label="manager-pending-answer",
        )

    assert calls == 2
    assert caught.value.phase == "backend"
    assert caught.value.cause == "401 Missing bearer"
    assert caught.value.attempts == 2
    assert "login_required" in str(caught.value)


@pytest.mark.parametrize("fatal_error", [
    "Provider turn cap reached: this engineer-r1 call used 40 provider turns "
    "(allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP).",
    "local history: expected ordinal 401, got 400",
    "2026-09-07T04:28:00.401Z WARN local history projection failed",
    "mse=0.401",
])
def test_relay_non_auth_receipts_do_not_replay_or_set_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fatal_error: str,
) -> None:
    _configure_relay_credential(tmp_path, monkeypatch, "fake-relay-token")
    backend = AgentCliBackend(backend="codex")
    calls = 0
    history = (
        ["Reconnecting... 1/3 (HTTP 401 Unauthorized)"]
        if fatal_error.startswith("Provider turn cap") else []
    )

    def fake_run_exec(self: Any, **_kwargs: Any) -> AgentRunResult:
        nonlocal calls
        calls += 1
        return _make_cli_result(
            exit_code=1, fatal_error=fatal_error, stderr_lines=history,
        )

    monkeypatch.setattr(AgentCliRunner, "run_exec", fake_run_exec, raising=True)
    result = backend.run_exec(
        prompt="continue", options=RunnerOptions(skip_git_repo_check=True),
        run_label="engineer-r1",
    )
    assert calls == 1
    assert result.fatal_error == fatal_error
    assert result.stop_kind == "backend_unavailable"
    assert result.stderr_lines == history
    assert not backend._auth_failure_detected


def test_explicit_secret_snapshot_survives_per_call_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret-value")
    backend = AgentCliBackend(
        backend="codex",
        known_secret_values_override=("custom-vault-secret",),
    )

    backend._refresh_known_secret_values()

    assert "custom-vault-secret" in backend._known_secret_values
    assert "ambient-secret-value" in backend._known_secret_values


def test_run_exec_translates_options_and_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=tmp_path / ".argus")
    captured: dict[str, Any] = {}

    def fake_run_exec(self, prompt, resume_thread_id, options, run_label, **kwargs) -> AgentRunResult:
        captured["prompt"] = prompt
        captured["resume_thread_id"] = resume_thread_id
        captured["options"] = options
        captured["run_label"] = run_label
        assert isinstance(options, CliRunnerOptions)
        return _make_cli_result(
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

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

    options = RunnerOptions(
        model="gpt-5.4-mini",
        reasoning_effort="high",
        working_dir=str(tmp_path),
        extra_args=["-c", "config_profile=tb"],
        full_auto=True,
        sandbox_mode="read-only",
        force_safe_mode=True,
        disable_tools=True,
        skip_git_repo_check=True,
        dangerous_yolo=False,
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
    assert forwarded.force_safe_mode is True
    assert forwarded.disable_tools is True
    assert forwarded.skip_git_repo_check is True
    assert forwarded.dangerous_yolo is False
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
        json.loads(line) for line in (tmp_path / ".argus" / "usage.jsonl").read_text().splitlines()
    ]
    assert len(usage_rows) == 1
    assert usage_rows[0]["call_id"] == result.call_id
    assert result.call_id_log_correlated is True


@pytest.mark.parametrize("provider,model,usage_model,event", [
    pytest.param("opencode", "anthropic/claude-sonnet-4-5", "", {
        "type": "step_finish",
        "part": {
            "tokens": {
                "input": 100, "output": 20, "reasoning": 5,
                "cache": {"read": 40, "write": 0},
            },
            "cost": 0.0123, "reason": "stop",
        },
    }, id="opencode"),
    pytest.param("pi", "gpt-5.4-mini", "gpt-5.4-mini", {
        "type": "message_end",
        "message": {
            "role": "assistant", "model": "gpt-5.4-mini",
            "usage": {
                "input": 100, "output": 20, "cacheRead": 40, "cacheWrite": 0,
                "reasoning": 5, "cost": {"total": 0.0123},
            },
        },
    }, id="pi"),
])
def test_success_persists_provider_reported_cost(
    tmp_path, monkeypatch, provider, model, usage_model, event,
):
    backend = AgentCliBackend(backend=provider)
    project = tmp_path / ".argus"
    backend.set_usage_context(project_root=project)
    monkeypatch.setattr(
        AgentCliRunner, "run_exec",
        lambda self, **kwargs: _make_cli_result(
            json_events=[event], thread_id=f"{provider}-cost-thread", usage_model=usage_model,
        ),
    )
    result = backend.run_exec(
        prompt=f"priced {provider} call", options=RunnerOptions(model=model),
        run_label="engineer-r1",
    )
    usage_row = json.loads((project / "usage.jsonl").read_text().strip())
    assert result.cost_usd == pytest.approx(0.0123)
    assert result.pricing_status == "priced"
    assert usage_row["cost_usd"] == pytest.approx(0.0123)
    assert usage_row["pricing_tier"] == "provider_reported"
    assert usage_row["cost_basis"] == "provider_reported"


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
        "argus.core.cost_control.cost_control_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "argus.core.cost_control.reserve_call_budget",
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
        return _make_cli_result(
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

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
    result = backend.run_exec(
        prompt="complete one call",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )

    # No life.mission.completed event is written: this models SIGKILL after the
    # completed call returned. The daily aggregate still reads the durable call.
    from argus.life.supervisor import global_daily_spend

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
        backend._runner.__class__,
        "run_exec",
        lambda self, **kwargs: _make_cli_result(
            json_events=[
                {
                    "type": "token_count",
                    "input_tokens": 1_000,
                    "output_tokens": 100,
                }
            ],
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
    rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
    assert [row["type"] for row in rows] == [
        "budget.reservation.created",
        "agent.io.start",
        "agent.io.complete",
        "usage.recorded",
        "budget.reservation.settled",
    ]
    assert rows[0]["amount_usd"] == 0.0
    assert rows[-1]["cost_usd"] == pytest.approx(0.008)
    metrics = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()]
    provider_metric = next(row for row in metrics if row["name"] == "provider.call")
    assert provider_metric["labels"]["status"] == "completed"
    assert provider_metric["fields"]["call_id"] == result.call_id


def test_settled_call_cost_blocks_the_next_call_at_global_cap(
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
        return _make_cli_result(
            json_events=[
                {
                    "type": "token_count",
                    "input_tokens": 0,
                    "output_tokens": 1_000,
                }
            ],
            thread_id="overrun-thread",
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

    result = backend.run_exec(
        prompt="expensive single response",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )

    assert result.cost_usd == pytest.approx(0.03)
    rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
    settled = next(row for row in rows if row["type"] == "budget.reservation.settled")
    assert settled["amount_usd"] == 0.0
    assert "overrun_usd" not in settled
    metrics = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()]
    provider_metric = next(row for row in metrics if row["name"] == "provider.call")
    assert "reservation_usd" not in provider_metric["fields"]
    assert "overrun_usd" not in provider_metric["fields"]

    denied = backend.run_exec(
        prompt="next response",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r1",
    )
    assert denied.stop_kind == "budget_exhausted"
    assert "global daily budget exhausted" in str(denied.fatal_error)


def test_unpriced_call_blocks_provider_spawn_and_acknowledged_risk_still_obeys_cap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="mission-1")
    calls = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        calls.append(kwargs["run_label"])
        return _make_cli_result(
            json_events=[
                {
                    "type": "token_count",
                    "input_tokens": 100,
                    "output_tokens": 20,
                }
            ],
            thread_id=kwargs["run_label"],
        )

    monkeypatch.setattr(
        backend._runner.__class__,
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
        prompt="continue with known pricing",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="reviewer",
    )

    assert first.pricing_status == "unpriced"
    assert first.cost_usd is None
    assert calls == ["engineer-r1"]
    assert "unresolved provider cost" in second.fatal_error
    assert second.stop_kind == "cost_unreconciled"
    assert second.pricing_status == "not_billed"
    state = json.loads((root / "cost-control.json").read_text())
    assert [row["call_id"] for row in state["unresolved"]] == [first.call_id]
    from argus.core.cost_control import acknowledge_unpriced_call

    acknowledge_unpriced_call(
        global_root=root, project_id=project.name, call_id=first.call_id,
        liability_usd=1.0, reason="Operator accepts this one unresolved call",
    )
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1")
    denied = backend.run_exec(
        prompt="known cap reached",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="engineer-r2",
    )
    assert calls == ["engineer-r1"]
    assert denied.stop_kind == "budget_exhausted"
    assert denied.pricing_status == "not_billed"
    assert "global daily budget exhausted" in denied.fatal_error


def test_missing_copilot_resume_target_does_not_poison_cost_control(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: None,
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since",
        lambda *args, **kwargs: None,
    )
    backend = AgentCliBackend(backend="copilot")
    backend.set_usage_context(project_root=project, mission_id="mission-1")
    resumes: list[str | None] = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        resume = kwargs["resume_thread_id"]
        resumes.append(resume)
        if resume:
            return _make_cli_result(
                exit_code=1,
                thread_id=resume,
                fatal_error=("Error: No session, task, or name matched 'stale-thread'."),
            )
        return _make_cli_result(thread_id="fresh-thread")

    monkeypatch.setattr(
        backend._runner.__class__,
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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    backend = AgentCliBackend(backend="copilot")
    provider_prompts: list[str] = []

    def fake_run_exec(self, prompt, **kwargs) -> AgentRunResult:
        provider_prompts.append(prompt)
        assert self.event_callback is not None
        thread = threading.Thread(
            target=self.event_callback,
            args=("manager.stdout", '{"type":"agent_message","message":"thinking"}'),
        )
        thread.start()
        thread.join()
        self.event_callback("stderr", "tool stderr line")
        return _make_cli_result(
            command=["copilot", "-p", "<prompt>"],
            agent_messages=["final answer"],
            json_events=[{"type": "agent_message", "message": "thinking"}],
            stdout_lines=['{"type":"agent_message","message":"thinking"}'],
            stderr_lines=["tool stderr line"],
            thread_id="thread-1",
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

    backend.run_exec(
        prompt="full prompt text",
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="manager",
        resume_thread_id="old-thread",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    raw_rows = [json.loads(line) for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()]
    assert all(row["event_schema_version"] == 1 for row in rows)
    assert all("event_validation" not in row for row in rows)
    assert [row["type"] for row in rows] == [
        "agent.io.start",
        "agent.io.complete",
        "usage.recorded",
    ]
    assert [row["type"] for row in raw_rows] == [
        "agent.io.start",
        "agent.io.stream",
        "agent.io.stream",
    ]
    assert [row["io_kind"] for row in rows[:-1]] == ["start", "complete"]
    assert [row["io_kind"] for row in raw_rows] == ["start", "stream", "stream"]
    # Call-bound tool instructions are part of the actual provider input. The
    # trace must retain that complete input, including the original request.
    assert len(provider_prompts) == 1
    assert raw_rows[0]["prompt"] == provider_prompts[0]
    assert raw_rows[0]["prompt"].startswith("full prompt text")
    assert rows[0]["run_label"] == "manager"
    assert [row["stream"] for row in raw_rows[1:]] == [
        "stdout",
        "stderr",
    ]
    assert raw_rows[1]["stream"] == "stdout"
    assert raw_rows[1]["model"] == "gpt-5.5"
    assert raw_rows[1]["line"].startswith('{"type"')
    assert raw_rows[2]["stream"] == "stderr"
    assert raw_rows[2]["model"] == "gpt-5.5"
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
    usage_rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert len(usage_rows) == 1
    assert usage_rows[0]["call_id"] == rows[-2]["call_id"]


def test_full_agent_io_batches_raw_stream_writes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.adapters.agent_cli_backend import _io_log

    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_BATCH_BYTES", "65536")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_FLUSH_INTERVAL_S", "60")
    batch_sizes: list[int] = []
    original_append = _io_log._jsonl_append_lines

    def recording_append(path, lines, lock):  # noqa: ANN001
        batch_sizes.append(len(lines))
        original_append(path, lines, lock)

    monkeypatch.setattr(_io_log, "_jsonl_append_lines", recording_append)
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        assert self.event_callback is not None
        for index in range(1_000):
            self.event_callback(
                "stdout",
                json.dumps(
                    {
                        "type": "assistant.tool_call_delta",
                        "data": {"index": index, "delta": "x" * 32},
                    }
                ),
            )
        return _make_cli_result(
            agent_messages=["done"],
            stdout_lines=["tail"],
            thread_id="batch-thread",
        )

    monkeypatch.setattr(
        backend._runner.__class__,
        "run_exec",
        fake_run_exec,
        raising=True,
    )
    backend.run_exec(
        prompt="batch",
        options=RunnerOptions(model="gpt-5.5", working_dir=str(tmp_path)),
        run_label="engineer-r1",
    )

    rows = [json.loads(line) for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()]
    assert sum(batch_sizes) == 1_000
    assert len(batch_sizes) < 10
    assert sum(row["type"] == "agent.io.stream" for row in rows) == 1_000
    control_rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert "json_events" not in next(
        row for row in control_rows if row["type"] == "agent.io.complete"
    )


def test_full_io_persists_prompt_once_not_as_user_message_echo(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_MODE", "full")
    backend = AgentCliBackend(backend="copilot")
    prompt = "large prompt body that must be stored exactly once"
    provider_prompts: list[str] = []

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        provider_prompts.append(kwargs["prompt"])
        assert self.event_callback is not None
        self.event_callback(
            "stdout",
            json.dumps(
                {
                    "type": "user.message",
                    "data": {"content": kwargs["prompt"]},
                }
            ),
        )
        self.event_callback(
            "stdout",
            json.dumps(
                {
                    "type": "assistant.message_delta",
                    "data": {"deltaContent": "ok"},
                }
            ),
        )
        return _make_cli_result(
            agent_messages=["ok"],
            thread_id="prompt-once",
        )

    monkeypatch.setattr(
        backend._runner.__class__,
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
    raw_rows = [json.loads(line) for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()]
    start = next(row for row in rows if row["type"] == "agent.io.start")
    raw_start = next(row for row in raw_rows if row["type"] == "agent.io.start")
    streams = [row for row in raw_rows if row["type"] == "agent.io.stream"]
    assert "prompt" not in start
    assert "prompt_sha256" not in start
    assert "prompt_sha256" not in raw_start
    assert len(provider_prompts) == 1
    assert raw_start["prompt"] == provider_prompts[0]
    assert raw_start["prompt"].startswith(prompt)
    assert len(streams) == 1
    assert "assistant.message_delta" in streams[0]["line"]


def test_copilot_run_exec_uses_exact_session_store_tokens(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(log_path))
    backend = AgentCliBackend(backend="copilot")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return _make_cli_result(
            agent_messages=["OK"],
            json_events=[{"type": "result", "usage": {"premiumRequests": 1.0}}],
            thread_id="session-1",
        )

    exact = CopilotCallUsage(
        (
            CopilotModelUsage(
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
            ),
        )
    )
    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since",
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
        return _make_cli_result(
            agent_messages=["OK"],
            json_events=[
                {
                    "type": "result",
                    "usage": {"premiumRequests": next(raw_totals)},
                }
            ],
            thread_id="resumed-session",
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since",
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

    rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="copilot")

    monkeypatch.setattr(
        backend._runner.__class__,
        "run_exec",
        lambda self, **kwargs: _make_cli_result(
            agent_messages=["OK"],
            thread_id="session-mini",
            usage_model="gpt-5.4-mini",
        ),
        raising=True,
    )
    mislabeled = CopilotCallUsage(
        (
            CopilotModelUsage(
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
            ),
        )
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: object(),
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since",
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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
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

    resolved = backend._agent_io_log_path(RunnerOptions(working_dir=str(tmp_path / "worktree")))

    assert resolved == project / "events.jsonl"
    migrated = [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines()]
    assert migrated == [{"type": "agent.io.start", "call_id": "old-call"}]
    assert (project / "events.migration-v2.json").exists()


def test_default_agent_io_is_bounded_and_drops_duplicate_stream(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
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
        self.event_callback(
            "stdout", '{"type":"assistant.message_delta","data":{"deltaContent":"huge"}}'
        )
        return _make_cli_result(
            command=["copilot", "-p", "HUGE PROMPT"],
            agent_messages=["result"],
            json_events=[{"large": "payload"}],
            stdout_lines=["huge stream payload"],
            stderr_lines=[],
            thread_id="compact-thread",
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
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
    assert "prompt_sha256" not in rows[0]
    assert rows[1]["command"] == ["copilot", "-p", "<prompt>"]
    assert "agent_messages" not in rows[1]
    assert "stdout_lines" not in rows[1]
    assert "stderr_lines" not in rows[1]
    assert "json_events" not in rows[1]
    assert rows[1]["stdout_line_count"] == 1
    assert rows[1]["json_event_count"] == 1
    assert rows[1]["agent_message_count"] == 1
    assert rows[1]["agent_message_chars"] == len("result")
    assert "last_agent_message_sha256" not in rows[1]
    assert len(live) == 1
    assert "assistant.message_delta" in live[0][1]


def test_codex_quota_events_and_daily_denial(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
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
        return _make_cli_result(agent_messages=["ok"], thread_id="codex-thread")

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
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
    usage_rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
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


@pytest.mark.parametrize("attempt", [1, 100])
def test_run_exec_normalizes_recoverable_reconnect_notice(
    monkeypatch: pytest.MonkeyPatch, attempt: int,
) -> None:
    backend = AgentCliBackend(backend="codex")

    def fake_run_exec(self, **kwargs) -> AgentRunResult:
        return _make_cli_result(
            agent_messages=["continued after reconnect"],
            fatal_error=(
                f"Reconnecting... {attempt}/100 "
                "(stream disconnected before completion: response.failed event received)"
            ),
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

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

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
    result = backend.run_exec(
        prompt="x",
        options=RunnerOptions(),
        run_label="reviewer",
    )

    assert result.fatal_error == "Error: Access denied by policy settings"
    assert backend._auth_failure_detected is True
    from argus.provider_integrations.copilot_guard import copilot_guard_snapshot

    assert copilot_guard_snapshot()["blocked_until"] > 0


def test_oauth_refresh_timeout_is_a_permanent_auth_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="pi")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return AgentRunResult(
            command=["pi"],
            exit_code=1,
            thread_id=None,
            agent_messages=[],
            json_events=[],
            stdout_lines=[],
            stderr_lines=[],
            turn_completed=False,
            turn_failed=True,
            fatal_error=(
                "OAuth refresh failed for github-copilot: The operation timed out."
            ),
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)
    result = backend.run_exec(
        prompt="x",
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert result.stop_kind == "permanent_error"
    assert backend._auth_failure_detected is True


def test_failed_manager_timeout_preserves_429_as_provider_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")

    def fake_run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        return _make_cli_result(
            exit_code=-15,
            fatal_error=(
                "External interrupt: Manager turn wall-clock limit reached "
                "after 300s; yield for review/steering"
            ),
            stderr_lines=[
                "Reconnecting... 37/100 (429 Too Many Requests; retry after 60s)"
            ],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

    result = backend.run_exec(
        prompt="classify",
        options=RunnerOptions(),
        run_label="manager-classify-grounded",
    )

    assert "wall-clock limit reached" in str(result.fatal_error)
    assert result.stop_kind == "provider_cooldown"


@pytest.mark.parametrize("error,exit_code,detail", [
    (FileNotFoundError("codex: not found"), 127, "not found"),
    (RuntimeError("subprocess died"), -1, "RuntimeError"),
])
def test_run_exec_handles_subprocess_failure(monkeypatch, error, exit_code, detail):
    backend = AgentCliBackend(backend="codex")

    def boom(self, **kwargs):
        raise error

    monkeypatch.setattr(AgentCliRunner, "run_exec", boom)
    result = backend.run_exec(
        prompt="anything", options=RunnerOptions(model="gpt-5.4-mini"),
        run_label="engineer-r1",
    )
    assert result.exit_code == exit_code
    assert result.fatal_error is not None
    assert detail in result.fatal_error
    assert result.agent_messages == []


@pytest.mark.parametrize("events,expected", [
    pytest.param(None, (0, 0, 0, 0), id="missing"),
    pytest.param([], (0, 0, 0, 0), id="empty"),
    pytest.param([
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 30},
        {"type": "agent_message", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 250, "cached_input_tokens": 25, "output_tokens": 80},
    ], (250, 25, 80, 0), id="latest-nonzero"),
    pytest.param([
        {"type": "token_count", "input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 30},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 150, "cached_input_tokens": 0, "output_tokens": 40},
        },
    ], (150, 0, 40, 0), id="final-tuple-clears-cached"),
    pytest.param([{
        "type": "msg",
        "content": {"input_tokens": 42, "cached_input_tokens": 5, "output_tokens": 7},
    }], (42, 5, 7, 0), id="nested-content"),
    pytest.param([
        {"type": "token_count", "input_tokens": 17, "cached_input_tokens": 4, "output_tokens": 3},
    ], (17, 4, 3, 0), id="top-level-cached"),
    # Codex >=0.121 moved metering to turn.completed. Ignoring it lost all cost.
    pytest.param([
        {"type": "thread.started", "thread_id": "x"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12944, "cached_input_tokens": 1234, "output_tokens": 75},
        },
    ], (12944, 1234, 75, 0), id="codex-0.121"),
    pytest.param([
        {"type": "thread.started", "thread_id": "x"},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 954691, "cached_input_tokens": 846976,
                "output_tokens": 11399, "reasoning_output_tokens": 4459,
            },
        },
    ], (954691, 846976, 11399, 4459), id="reasoning-output"),
])
def test_token_count_extraction(events, expected):
    assert sum_token_counts(events) == expected


def test_claude_message_usage_sums_turns_and_cache_aliases() -> None:
    usage = extract_token_usage(
        [
            {
                "type": "assistant",
                "message": {
                    "model": "glm-5.2",
                    "usage": {
                        "input_tokens": 120,
                        "cache_read_input_tokens": 80,
                        "cache_creation_input_tokens": 20,
                        "output_tokens": 15,
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "model": "glm-5.2",
                    "usage": {
                        "input_tokens": 60,
                        "cache_read_input_tokens": 40,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 10,
                    },
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "num_turns": 2,
                "total_cost_usd": 0.25,
            },
        ]
    )

    assert usage.source == "per_message"
    assert usage.as_tuple() == (180, 120, 25, 0)
    assert usage.cache_write_tokens == 20
    assert usage.provider_cost_usd == pytest.approx(0.25)


def test_claude_result_usage_accepts_openai_and_anthropic_aliases() -> None:
    usage = extract_token_usage(
        [
            {
                "type": "result",
                "usage": {
                    "prompt_tokens": 100,
                    "cache_read_input_tokens": 70,
                    "cache_creation_input_tokens": 5,
                    "completion_tokens": 20,
                    "reasoning_tokens": 3,
                },
            }
        ]
    )

    assert usage.as_tuple() == (100, 70, 20, 3)
    assert usage.cache_write_tokens == 5


def test_claude_request_unit_placeholders_are_not_recorded_as_tokens() -> None:
    usage = extract_token_usage(
        [
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 1, "output_tokens": 1}},
            },
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 1, "output_tokens": 1}},
            },
            {
                "type": "result",
                "num_turns": 2,
                "usage": {"input_tokens": 2, "output_tokens": 2},
                "total_cost_usd": 0.1,
            },
        ]
    )

    assert usage.source == "provider_request_units"
    assert usage.observed is False
    assert usage.as_tuple() == (0, 0, 0, 0)
    assert usage.provider_cost_usd == pytest.approx(0.1)


def test_claude_per_message_usage_beats_result_turn_count_placeholder() -> None:
    usage = extract_token_usage(
        [
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 120, "output_tokens": 15}},
            },
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 80, "output_tokens": 10}},
            },
            {
                "type": "result",
                "num_turns": 2,
                "usage": {"input_tokens": 2, "output_tokens": 2},
            },
        ]
    )

    assert usage.source == "per_message"
    assert usage.as_tuple() == (200, 0, 25, 0)


def test_usage_delta_for_thread_decumulates_reasoning_output_tokens() -> None:
    backend = AgentCliBackend(backend="codex")
    assert backend._usage.usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(100, 10, 20, 7),
    ) == (100, 10, 20, 7)
    assert backend._usage.usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(160, 30, 45, 19),
    ) == (60, 20, 25, 12)
    assert backend._usage.usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(20, 5, 6, 2),
    ) == (20, 5, 6, 2)


def test_run_exec_forwards_ordered_native_skill_roots(
    tmp_path: Path,
    cli_call,
) -> None:
    native_root = tmp_path / "repo" / ".agents" / "skills"
    managed_root = tmp_path / "state" / "skills" / "engineer"
    native_root.mkdir(parents=True)
    managed_root.mkdir(parents=True)
    backend = AgentCliBackend(backend="pi")
    backend.run_exec(
        prompt="discover the project Skill",
        options=RunnerOptions(
            skill_paths=[str(native_root.resolve()), str(managed_root.resolve())]
        ),
        run_label="engineer-r1",
    )

    assert cli_call.call_args.kwargs["options"].skill_paths == [
        str(native_root.resolve()),
        str(managed_root.resolve()),
    ]


@pytest.mark.parametrize("use_defaults,expected", [
    (False, (120, 300, 600)), (True, (300, 900, 1800)),
])
def test_run_exec_forwards_watchdog_hooks(
    cli_call, use_defaults: bool, expected: tuple[int, int, int],
) -> None:
    """Watchdog hooks on argus RunnerOptions must reach the bundled runner.

    A MissionDaemon-driven supervisor passes ``external_interrupt_reason_provider``
    so it can interrupt a long-running engineer turn promptly when an
    operator sends ``/inject`` or ``/stop``. If the adapter drops these
    fields, /inject becomes ineffective during a round.
    """

    request_identity = ContextVar("watchdog_request", default="outside")
    stopped = threading.Event()
    interrupt_calls: list[str] = []

    def interrupt_provider() -> str | None:
        identity = request_identity.get()
        interrupt_calls.append(identity)
        return f"stop {identity}" if stopped.is_set() else None

    def inactivity_callback(snapshot: Any) -> str | None:  # noqa: ARG001
        return None

    backend = AgentCliBackend(
        backend="codex",
        default_interrupt_reason_provider=interrupt_provider if use_defaults else None,
        default_watchdog_soft_idle_seconds=300,
        default_watchdog_stalled_idle_seconds=900,
        default_watchdog_hard_idle_seconds=1800,
    )
    options = RunnerOptions(
        model="gpt-5.4-mini",
        external_interrupt_reason_provider=None if use_defaults else interrupt_provider,
        inactivity_callback=inactivity_callback,
        watchdog_soft_idle_seconds=None if use_defaults else 120,
        watchdog_stalled_idle_seconds=None if use_defaults else 300,
        watchdog_hard_idle_seconds=None if use_defaults else 600,
    )
    token = request_identity.set("current-request")
    try:
        backend.run_exec(prompt="x", options=options, run_label="main")
    finally:
        request_identity.reset(token)

    forwarded = cli_call.call_args.kwargs["options"]
    callback = forwarded.external_interrupt_reason_provider
    assert callable(callback)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(request_identity.get).result(timeout=1) == "outside"
        assert pool.submit(callback).result(timeout=1) is None
        stopped.set()
        assert pool.submit(callback).result(timeout=1) == "stop current-request"
        assert pool.submit(request_identity.get).result(timeout=1) == "outside"
    assert interrupt_calls and set(interrupt_calls) == {"current-request"}
    assert request_identity.get() == "outside"
    assert forwarded.inactivity_callback is inactivity_callback
    assert (
        forwarded.watchdog_soft_idle_seconds,
        forwarded.watchdog_stalled_idle_seconds,
        forwarded.watchdog_hard_idle_seconds,
    ) == expected


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
        backend._runner.__class__,
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
    assert result.fatal_error == ("External interrupt: operator abort requested: stop now")


def test_run_exec_allows_per_call_watchdog_disable(
    cli_call,
) -> None:
    backend = AgentCliBackend(backend="codex")
    backend.run_exec(
        prompt="x",
        options=RunnerOptions(
            model="gpt-5.4-mini",
            watchdog_soft_idle_seconds=0,
            watchdog_stalled_idle_seconds=0,
            watchdog_hard_idle_seconds=0,
        ),
        run_label="main",
    )

    forwarded = cli_call.call_args.kwargs["options"]
    assert forwarded.watchdog_soft_idle_seconds == 0
    assert forwarded.watchdog_stalled_idle_seconds == 0
    assert forwarded.watchdog_hard_idle_seconds == 0


def test_run_exec_composes_explicit_watchdog_with_defaults(
    cli_call,
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
        default_watchdog_stalled_idle_seconds=900,
        default_watchdog_hard_idle_seconds=1800,
    )

    backend.run_exec(
        prompt="x",
        options=RunnerOptions(
            model="gpt-5.4-mini",
            external_interrupt_reason_provider=explicit_interrupt,
            watchdog_soft_idle_seconds=10,
            watchdog_stalled_idle_seconds=15,
            watchdog_hard_idle_seconds=20,
        ),
        run_label="main",
    )

    forwarded = cli_call.call_args.kwargs["options"]
    assert forwarded.external_interrupt_reason_provider is not explicit_interrupt
    assert forwarded.external_interrupt_reason_provider() == "stale"
    assert calls == ["default", "explicit"]
    assert forwarded.watchdog_soft_idle_seconds == 10
    assert forwarded.watchdog_stalled_idle_seconds == 15
    assert forwarded.watchdog_hard_idle_seconds == 20


def test_run_exec_reports_delta_for_resumed_cumulative_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="codex")
    raw_usages = [
        {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 100},
        {"input_tokens": 1250, "cached_input_tokens": 500, "output_tokens": 130},
    ]

    def fake_run_exec(self, **kwargs) -> AgentRunResult:
        usage = raw_usages.pop(0)
        return _make_cli_result(
            thread_id="thr-cumulative",
            json_events=[{"type": "turn.completed", "usage": usage}],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

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


def test_run_exec_preserves_resumed_opencode_per_step_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AgentCliBackend(backend="opencode")
    raw_usages = [
        {
            "input": 100,
            "output": 10,
            "cache": {"read": 20, "write": 0},
            "cost": 0.01,
        },
        {
            "input": 150,
            "output": 20,
            "cache": {"read": 30, "write": 0},
            "cost": 0.02,
        },
    ]

    def fake_run_exec(self, **kwargs) -> AgentRunResult:
        usage = raw_usages.pop(0)
        return _make_cli_result(
            thread_id="ses-opencode",
            json_events=[
                {
                    "type": "step_finish",
                    "part": {
                        "tokens": {key: value for key, value in usage.items() if key != "cost"},
                        "cost": usage["cost"],
                    },
                },
            ],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec, raising=True)

    first = backend.run_exec(
        prompt="first",
        options=RunnerOptions(model="openai/gpt-5.4"),
        run_label="engineer-r1",
    )
    second = backend.run_exec(
        prompt="second",
        options=RunnerOptions(model="openai/gpt-5.4"),
        run_label="engineer-r2",
        resume_thread_id="ses-opencode",
    )

    assert (first.input_tokens, first.cached_input_tokens, first.output_tokens) == (
        120,
        20,
        10,
    )
    assert (second.input_tokens, second.cached_input_tokens, second.output_tokens) == (
        180,
        30,
        20,
    )
    assert first.cost_usd == pytest.approx(0.01)
    assert second.cost_usd == pytest.approx(0.02)


def test_run_exec_default_watchdog_options_inherit_backend_defaults():
    options = RunnerOptions(model="gpt-5.4-mini")
    assert options.external_interrupt_reason_provider is None
    assert options.inactivity_callback is None
    assert options.watchdog_soft_idle_seconds is None
    assert options.watchdog_stalled_idle_seconds is None
    assert options.watchdog_hard_idle_seconds is None


def test_build_agent_cli_backend_from_env_uses_env(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_EXTRA_ARGS", '-c "model_profile=fast"')
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", "120")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS", "600")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "900")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)

    backend = build_agent_cli_backend_from_env()
    inner = backend._runner
    # The bundled runner stores the backend name on the inner runner.
    assert inner.backend == "claude"
    assert inner.default_extra_args == ["-c", "model_profile=fast"]
    assert backend._default_watchdog_soft_idle_seconds == 120
    assert backend._default_watchdog_stalled_idle_seconds == 600
    assert backend._default_watchdog_hard_idle_seconds == 900


def test_fork_creates_independent_runner_with_same_usage_context(tmp_path: Path) -> None:
    backend = AgentCliBackend(
        backend="copilot",
        runner_bin="/bin/echo",
        default_extra_args=["--trace"],
        default_watchdog_soft_idle_seconds=11,
        default_watchdog_stalled_idle_seconds=22,
        default_watchdog_hard_idle_seconds=33,
    )
    backend.set_usage_context(
        project_root=tmp_path / "project",
        global_root=tmp_path,
        mission_id="review",
    )

    forked = backend.fork()

    assert forked is not backend
    assert forked._runner is not backend._runner
    assert forked._runner.agent_bin == backend._runner.agent_bin
    assert forked._runner.default_extra_args == ["--trace"]
    assert forked._usage_context_snapshot() == backend._usage_context_snapshot()
    forked.close_acp_clients()


def test_background_forks_do_not_publish_into_the_foreground_role(tmp_path: Path) -> None:
    foreground, background = [], []
    backend = AgentCliBackend(backend="pi", runner_bin="/bin/echo", event_callback=lambda *event: foreground.append(event))
    backend.set_usage_context(project_root=tmp_path / "project", mission_id="mission", global_root=tmp_path)
    inherited = backend.fork()
    isolated = backend.fork(event_callback=None)
    redirected = backend.fork(event_callback=lambda *event: background.append(event))
    line = '{"type":"tool_execution_start","toolName":"read"}'
    for target in (inherited, isolated, redirected):
        target._io_logger.stream_event_callback("engineer.stdout", line, backend_name="pi", known_secret_values=())
        assert target._usage_context_snapshot() == backend._usage_context_snapshot()
        target.close_acp_clients()
    assert foreground == [("engineer.stdout", line)]
    assert background == [("engineer.stdout", line)]
    assert backend._io_logger.external_event_callback is inherited._io_logger.external_event_callback


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
    assert backend._runner.default_extra_args == ["--trace"]


def test_build_agent_cli_backend_from_env_defaults(monkeypatch):
    for name in (
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_RUNNER_BIN",
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
        "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS",
        "ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS",
        "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    backend = build_agent_cli_backend_from_env()
    # The bundled runner's default is codex.
    assert backend._runner.backend == "codex"
    assert backend._runner.default_extra_args == []
    assert backend._default_watchdog_soft_idle_seconds == 600
    assert backend._default_watchdog_stalled_idle_seconds == 1800
    assert backend._default_watchdog_hard_idle_seconds == 0


def test_build_backend_default_does_not_reuse_persisted_dsh_runner(
    monkeypatch,
):
    from argus.adapters.agent_cli_backend import _core
    from argus.core import knob_store

    monkeypatch.setattr(
        knob_store,
        "read_persisted_knobs",
        lambda: {
            "ARGUS_SKILL_RUNNER_BACKEND": "dsh",
            "ARGUS_SKILL_RUNNER_BIN": "/persisted/dsh",
        },
    )
    captured = {}
    monkeypatch.setattr(
        _core,
        "AgentCliBackend",
        lambda **kwargs: captured.update(kwargs) or captured,
    )

    for configured_backend in (None, "   "):
        if configured_backend is None:
            monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
        else:
            monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", configured_backend)
        monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
        captured.clear()

        assert build_agent_cli_backend_from_env() is captured
        assert captured["backend"] == "codex"
        assert captured["runner_bin"] is None


@pytest.mark.parametrize("observed", [False, True])
def test_context_parser_failure_uses_trusted_completion_receipt(tmp_path, monkeypatch, observed):
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "0")
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.capture_copilot_usage_cursor",
        lambda: None,
    )
    monkeypatch.setattr(
        "argus.adapters.agent_cli_backend._exec_spawn.read_copilot_usage_since",
        lambda *args, **kwargs: None,
    )
    backend = AgentCliBackend(backend="copilot")
    backend.set_usage_context(project_root=project, mission_id="mission-1")

    def fake_run_exec(self, **kwargs):
        return _make_cli_result(
            command=["copilot", "--context", "default"], exit_code=1,
            thread_id=None, fatal_error="Process exited with code 1 before turn completion.",
            stderr_lines=["error: unknown option '--context'", "(Did you mean --connect?)",
                          "", "Try 'copilot --help' for more information."],
            stdout_lines=["model/tool output"] if observed else [],
        )

    monkeypatch.setattr(backend._runner.__class__, "run_exec", fake_run_exec)
    result = backend.run_exec(prompt="test", options=RunnerOptions(model="gpt-6-astra"),
                              run_label="manager-classify-grounded-retry")
    assert result.pricing_status == ("partial" if observed else "not_billed")
    assert result.cost_usd == (None if observed else 0.0)
