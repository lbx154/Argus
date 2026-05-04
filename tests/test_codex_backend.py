"""Tests for ``argus_skill.adapters.codex_backend``.

We do NOT spawn a real codex / claude CLI in CI. Instead we monkey-patch
the underlying ``CodexRunner.run_exec`` to return a synthetic
``CodexRunResult``, then verify our adapter:

  * Translates argus-skill ``RunnerOptions`` → ArgusBot ``RunnerOptions``
    correctly (model, reasoning_effort, working_dir, extra_args,
    full_auto, skip_git_repo_check, dangerous_yolo).
  * Translates ``CodexRunResult`` → argus-skill ``RunnerResult``
    correctly, including agent_messages, stdout/stderr lines, thread_id,
    fatal_error.
  * Sums token counts from the JSON event stream (last non-zero wins).
  * Catches subprocess failures (FileNotFoundError, generic exceptions)
    and surfaces them as a ``RunnerResult`` with ``fatal_error`` set.
  * ``build_codex_backend_from_env`` honours env vars.
"""
from __future__ import annotations

from typing import Any

import pytest

from argus_skill.adapters.codex_backend import (
    CodexRunnerBackend,
    _sum_token_counts,
    build_codex_backend_from_env,
)
from argus_skill.core.models import RunnerOptions

# Skip the entire module if ArgusBot isn't importable. Locally we have it
# pip-installed, but downstream consumers might run argus-skill alone.
pytest.importorskip("codex_autoloop.codex_runner")


from codex_autoloop.codex_runner import RunnerOptions as ArgusRunnerOptions  # noqa: E402
from codex_autoloop.models import CodexRunResult  # noqa: E402


def _make_argus_result(
    *,
    exit_code: int = 0,
    agent_messages: list[str] | None = None,
    json_events: list[dict[str, Any]] | None = None,
    thread_id: str | None = "thr-abc123",
    fatal_error: str | None = None,
    stdout_lines: list[str] | None = None,
    stderr_lines: list[str] | None = None,
) -> CodexRunResult:
    return CodexRunResult(
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


def test_run_exec_translates_options_and_result(monkeypatch):
    backend = CodexRunnerBackend(backend="codex")
    captured: dict[str, Any] = {}

    def fake_run_exec(self, *, prompt, resume_thread_id, options, run_label):
        captured["prompt"] = prompt
        captured["resume_thread_id"] = resume_thread_id
        captured["options"] = options
        captured["run_label"] = run_label
        assert isinstance(options, ArgusRunnerOptions)
        return _make_argus_result(
            agent_messages=["hello world", "final answer"],
            json_events=[
                {"type": "token_count", "input_tokens": 100, "output_tokens": 50},
                {"type": "token_count", "input_tokens": 250, "output_tokens": 75},
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
    assert result.output_tokens == 75


def test_run_exec_handles_file_not_found(monkeypatch):
    backend = CodexRunnerBackend(backend="codex")

    def boom(self, *, prompt, resume_thread_id, options, run_label):
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


def test_run_exec_handles_generic_exception(monkeypatch):
    backend = CodexRunnerBackend(backend="codex")

    def boom(self, *, prompt, resume_thread_id, options, run_label):
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
    in_tok, out_tok = _sum_token_counts(None)
    assert (in_tok, out_tok) == (0, 0)
    in_tok, out_tok = _sum_token_counts([])
    assert (in_tok, out_tok) == (0, 0)


def test_token_count_extraction_picks_latest_nonzero():
    events = [
        {"type": "agent_message", "input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 100, "output_tokens": 30},
        # a later event with zero tokens shouldn't overwrite the earlier non-zero
        {"type": "agent_message", "input_tokens": 0, "output_tokens": 0},
        {"type": "token_count", "input_tokens": 250, "output_tokens": 80},
    ]
    in_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, out_tok) == (250, 80)


def test_token_count_extraction_handles_nested_content():
    events = [
        {
            "type": "msg",
            "content": {"input_tokens": 42, "output_tokens": 7},
        }
    ]
    in_tok, out_tok = _sum_token_counts(events)
    assert (in_tok, out_tok) == (42, 7)


def test_build_codex_backend_from_env_uses_env(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_EXTRA_ARGS", '-c "model_profile=fast"')
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)

    backend = build_codex_backend_from_env()
    inner = backend._argus_runner
    # ArgusBot stores backend on the inner runner.
    assert inner.backend == "claude"
    assert inner.default_extra_args == ["-c", "model_profile=fast"]


def test_build_codex_backend_from_env_defaults(monkeypatch):
    for name in (
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_RUNNER_BIN",
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
    ):
        monkeypatch.delenv(name, raising=False)
    backend = build_codex_backend_from_env()
    # ArgusBot's default is codex.
    assert backend._argus_runner.backend == "codex"
    assert backend._argus_runner.default_extra_args == []
