"""Budgeted Codex boundary for supervised subagent decisions."""
from __future__ import annotations

import time
from pathlib import Path

from ...adapters.agent_cli_backend import AgentCliBackend
from ...core.models import RunnerOptions, RunnerResult
from ...core.paths import session_state_root
from ...core.project import project_fingerprint
from ...core.run_gateway import run_exec as gateway_run_exec
from ...core.sandbox import engineer_sandbox_mode
from ._registry import _add_usage_totals
from ._text import _find_codex

_CODEX_BACKEND: AgentCliBackend | None = None


def _codex_backend() -> AgentCliBackend:
    global _CODEX_BACKEND
    if _CODEX_BACKEND is None:
        _CODEX_BACKEND = AgentCliBackend(
            backend="codex",
            runner_bin=_find_codex(),
            default_watchdog_soft_idle_seconds=0,
            default_watchdog_stalled_idle_seconds=0,
            default_watchdog_hard_idle_seconds=0,
        )
    return _CODEX_BACKEND


def _usage_project_root(cwd: str) -> Path:
    identity = project_fingerprint(cwd)
    return session_state_root(identity.fingerprint)


def _run_backend_turn(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None,
    timeout: int,
    run_label: str,
    mission_id: str | None = None,
) -> RunnerResult:
    backend = _codex_backend()
    backend.set_usage_context(
        project_root=_usage_project_root(cwd),
        mission_id=mission_id or run_label,
    )
    deadline = time.monotonic() + max(1, timeout)

    def timeout_reason() -> str | None:
        if time.monotonic() >= deadline:
            return f"subagent model turn exceeded {timeout}s"
        return None

    sandbox_mode = engineer_sandbox_mode()
    return gateway_run_exec(
        backend,
        prompt=prompt,
        options=RunnerOptions(
            model=model,
            reasoning_effort="low",
            working_dir=cwd,
            skip_git_repo_check=True,
            sandbox_mode=sandbox_mode,
            dangerous_yolo=sandbox_mode is None,
            external_interrupt_reason_provider=timeout_reason,
            watchdog_soft_idle_seconds=0,
            watchdog_stalled_idle_seconds=0,
            watchdog_hard_idle_seconds=timeout,
        ),
        run_label=run_label,
        resume_thread_id=thread_id,
    )


def _usage_tuple(result: RunnerResult) -> tuple[int, int, int, int]:
    return (
        int(result.input_tokens or 0),
        int(result.cached_input_tokens or 0),
        int(result.output_tokens or 0),
        int(result.reasoning_output_tokens or 0),
    )


def _run_codex_with_usage(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
    timeout: int = 120,
    *,
    run_label: str = "subagent",
    mission_id: str | None = None,
) -> tuple[list[str], str | None, tuple[int, int, int, int]]:
    """Run one metered supervisor turn, retrying a missing resumed thread once."""
    result = _run_backend_turn(
        prompt,
        model,
        cwd,
        thread_id,
        timeout,
        run_label,
        mission_id,
    )

    messages = list(result.agent_messages)
    new_thread_id = result.thread_id or thread_id
    usage = _usage_tuple(result)
    if thread_id and not messages and result.exit_code != -1:
        fresh = _run_backend_turn(
            prompt,
            model,
            cwd,
            None,
            timeout,
            f"{run_label}:resume-recovery",
            mission_id,
        )
        messages = list(fresh.agent_messages)
        new_thread_id = fresh.thread_id
        usage = _add_usage_totals(usage, _usage_tuple(fresh))
    return (messages, new_thread_id, usage)


def _run_codex(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
    timeout: int = 120,
) -> tuple[list[str], str | None]:
    """Backward-compatible wrapper returning only messages and thread id."""
    messages, new_thread_id, _usage = _run_codex_with_usage(
        prompt,
        model,
        cwd,
        thread_id,
        timeout,
    )
    return messages, new_thread_id
