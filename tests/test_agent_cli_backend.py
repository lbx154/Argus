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
import threading
from pathlib import Path
from typing import Any

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
from argus.core.token_usage import sum_token_counts
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
    # The call's usage lives in usage.jsonl alone.
    assert [row["type"] for row in rows] == ["agent.io.start", "agent.io.complete"]
    # The start row names the call once; each frame after it carries only
    # what differs per frame.
    assert raw_rows[0]["type"] == "agent.io.start"
    assert raw_rows[0]["model"] == "gpt-5.5"
    assert [set(row) for row in raw_rows[1:]] == [{"call_id", "stream", "line", "ts"}] * 2
    assert {row["call_id"] for row in raw_rows} == {rows[0]["call_id"]}
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
    assert raw_rows[1]["line"].startswith('{"type"')
    assert "agent_messages" not in rows[-1]
    assert "stdout_lines" not in rows[-1]
    assert "stderr_lines" not in rows[-1]
    assert "json_events" not in rows[-1]
    assert rows[-1]["agent_message_count"] == 1
    assert rows[-1]["stdout_line_count"] == 1
    assert rows[-1]["json_event_count"] == 1
    assert rows[-1]["command"] == ["copilot", "-p", "<prompt>"]
    assert rows[-1]["thread_id"] == "thread-1"
    usage_rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert len(usage_rows) == 1
    assert usage_rows[0]["call_id"] == rows[-1]["call_id"]
    assert usage_rows[0]["thread_id"] == "thread-1"
    assert usage_rows[0]["started_at"] <= usage_rows[0]["completed_at"]


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
                        "type": "tool.shell_output",
                        "data": {"index": index, "text": "x" * 32},
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
    assert sum("line" in row for row in rows) == 1_000
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
                    "type": "assistant.message",
                    "data": {"content": "ok"},
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
    raw_start = next(row for row in raw_rows if row.get("type") == "agent.io.start")
    streams = [row for row in raw_rows if "line" in row]
    assert "prompt" not in start
    assert "prompt_sha256" not in start
    assert "prompt_sha256" not in raw_start
    assert len(provider_prompts) == 1
    assert raw_start["prompt"] == provider_prompts[0]
    assert raw_start["prompt"].startswith(prompt)
    assert len(streams) == 1
    assert "assistant.message" in streams[0]["line"]


def test_transcript_drops_fragments_and_housekeeping_but_live_sees_them(tmp_path) -> None:
    from argus.adapters.agent_cli_backend._io_log import AgentIOLogger

    def frame(kind: str) -> str:
        return json.dumps({"type": kind, "data": {}})

    dropped = [
        # Copilot fragments the final message / tool result repeats in full.
        "assistant.message_delta", "assistant.reasoning_delta", "assistant.tool_call_delta",
        "assistant.message_start", "tool.execution_partial_result",
        # Copilot housekeeping.
        "session.background_tasks_changed", "session.mcp_servers_loaded",
        "session.mcp_server_status_changed", "session.mcp_server_tools_loaded",
        "session.tools_updated", "assistant.idle",
        "model.call_start", "model.call_finished", "model.call_final_result",
        # Pi fragments and aggregates.
        "message_start", "message_update", "tool_execution_update", "turn_end", "agent_end", "queue_update",
    ]
    kept = [
        "assistant.message", "assistant.reasoning", "tool.execution_start", "tool.shell_output",
        "tool.execution_complete", "session.usage_checkpoint", "result",
        "item.started", "item.completed", "assistant", "user", "message_end", "tool_execution_end",
    ]
    live: list[tuple[str, str]] = []
    logger = AgentIOLogger(external_event_callback=lambda *event: live.append(event))
    logger.start_call(call_id="c1", run_label="engineer-r1", log_path=tmp_path / "events.jsonl", prompt="p")
    for kind in dropped + kept:
        logger.stream_event_callback("engineer.stdout", frame(kind), known_secret_values=())
    logger.stream_event_callback("engineer.stderr", "plain diagnostic", known_secret_values=())
    logger.close("c1")

    rows = [json.loads(line) for line in (tmp_path / "agent_io.jsonl").read_text().splitlines()]
    assert [json.loads(row["line"])["type"] for row in rows[:-1]] == kept
    assert rows[-1]["line"] == "plain diagnostic" and rows[-1]["stream"] == "stderr"
    # "Typing" still reaches the live view; only the file goes without it.
    assert ("engineer.stdout", frame("assistant.message_delta")) in live


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
        lambda **_: object(),
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
    assert usage["model_usage"][0]["input_tokens"] == 25_819
    assert usage["model_usage"][0]["cost_usd"] == pytest.approx(0.161605)
    assert usage["thread_id"] == "session-1"


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
        lambda **_: object(),
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


def test_copilot_stream_model_overrides_mislabeled_usage_row(
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
        lambda **_: object(),
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
    # Totals that went down leave no baseline: the call's usage is unknown.
    assert backend._usage.usage_delta_for_thread(
        thread_id="t1",
        raw_totals=(20, 5, 6, 2),
    ) is None
    # So is a resumed thread's first call with no baseline anywhere.
    assert backend._usage.usage_delta_for_thread(
        thread_id="t2",
        raw_totals=(20, 5, 6, 2),
        resume_baseline_unknown=True,
    ) is None


def test_a_resumed_claude_session_records_its_runs_own_usage() -> None:
    # A claude result reports that -p run's usage; only codex reports thread totals.
    def result(input_tokens: int, output_tokens: int) -> AgentRunResult:
        return AgentRunResult(
            command=["claude"], exit_code=0, thread_id="session-1", turn_completed=True,
            json_events=[{"type": "result", "total_cost_usd": 0.01, "usage": {
                "input_tokens": input_tokens, "output_tokens": output_tokens,
            }}],
        )

    backend = AgentCliBackend(backend="claude")
    first = backend._translate_result(result(5000, 800))
    resumed = backend._translate_result(result(3000, 300), resume_thread_id="session-1")
    restarted = AgentCliBackend(backend="claude")._translate_result(
        result(3000, 300), resume_thread_id="session-1",
    )
    assert [(run.input_tokens, run.output_tokens, run.cost_usd) for run in (first, resumed, restarted)] == [
        (5000, 800, 0.01), (3000, 300, 0.01), (3000, 300, 0.01),
    ]
    assert resumed.input_tokens_present and restarted.input_tokens_present


def test_consumed_interrupt_returns_canonical_result_without_starting_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)

    backend = build_agent_cli_backend_from_env()
    inner = backend._runner
    # The bundled runner stores the backend name on the inner runner.
    assert inner.backend == "claude"
    assert inner.default_extra_args == ["-c", "model_profile=fast"]
    assert backend._default_watchdog_soft_idle_seconds == 120
    assert backend._default_watchdog_stalled_idle_seconds == 600
    assert backend._default_watchdog_hard_idle_seconds == 0


def test_background_forks_do_not_publish_into_the_foreground_role(tmp_path: Path) -> None:
    foreground, background = [], []
    backend = AgentCliBackend(backend="pi", runner_bin="/bin/echo", event_callback=lambda *event: foreground.append(event))
    backend.set_usage_context(project_root=tmp_path / "project", mission_id="mission", global_root=tmp_path)
    inherited = backend.fork()
    isolated = backend.fork(event_callback=None)
    redirected = backend.fork(event_callback=lambda *event: background.append(event))
    line = '{"type":"tool_execution_start","toolName":"read"}'
    for target in (inherited, isolated, redirected):
        target._io_logger.stream_event_callback("engineer.stdout", line, known_secret_values=())
        assert target._usage_context_snapshot() == backend._usage_context_snapshot()
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
