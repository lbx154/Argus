"""Budgeted agent-CLI boundary for supervised subagent decisions."""
from __future__ import annotations

from pathlib import Path

from ...adapters.agent_cli_backend import AgentCliBackend
from ...agent_cli.runner_backend import resolve_available_runner
from ...core.knobs import (
    resolve_role_backend,
    resolve_role_model,
    resolve_role_reasoning_effort,
    resolve_runner_bin_setting,
)
from ...core.models import RunnerOptions, RunnerResult
from ...core.paths import session_state_root
from ...core.project import project_fingerprint
from ...core.run_gateway import run_exec as gateway_run_exec
from ...core.sandbox import engineer_sandbox_mode
from ._registry import _add_usage_totals

_SUPERVISOR_BACKENDS: dict[tuple[str, str], AgentCliBackend] = {}


def _supervisor_backend() -> AgentCliBackend:
    """Return the configured supervisor backend, inheriting the fleet default."""
    requested = resolve_role_backend("supervisor")
    configured_bin = resolve_runner_bin_setting("supervisor") or None
    backend_name, runner_bin = resolve_available_runner(requested, configured_bin)
    key = (backend_name, runner_bin)
    backend = _SUPERVISOR_BACKENDS.get(key)
    if backend is None:
        backend = AgentCliBackend(
            backend=backend_name,
            runner_bin=runner_bin,
            default_watchdog_soft_idle_seconds=0,
            default_watchdog_stalled_idle_seconds=0,
            default_watchdog_hard_idle_seconds=0,
        )
        _SUPERVISOR_BACKENDS[key] = backend
    return backend


def resolve_supervisor_model() -> str:
    """Resolve the supervisor model through the shared role precedence."""
    return resolve_role_model(
        "supervisor",
        role_env="ARGUS_SKILL_SUPERVISOR_MODEL",
    )


def resolve_supervisor_reasoning_effort() -> str:
    """Resolve supervisor reasoning effort from env/persisted configuration."""
    return resolve_role_reasoning_effort(
        "ARGUS_SKILL_SUPERVISOR_REASONING_EFFORT",
        default="low",
    )


def _usage_project_root(cwd: str) -> Path:
    identity = project_fingerprint(cwd)
    return session_state_root(identity.fingerprint)


def _run_backend_turn(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None,
    run_label: str,
    mission_id: str | None = None,
) -> RunnerResult:
    backend = _supervisor_backend()
    backend.set_usage_context(
        project_root=_usage_project_root(cwd),
        mission_id=mission_id or run_label,
    )
    sandbox_mode = (
        engineer_sandbox_mode()
        if str(getattr(backend, "backend", "") or "") == "codex"
        else None
    )
    return gateway_run_exec(
        backend,
        prompt=prompt,
        options=RunnerOptions(
            model=model,
            reasoning_effort=resolve_supervisor_reasoning_effort(),
            working_dir=cwd,
            skip_git_repo_check=True,
            sandbox_mode=sandbox_mode,
            dangerous_yolo=sandbox_mode is None,
            watchdog_soft_idle_seconds=0,
            watchdog_stalled_idle_seconds=0,
            watchdog_hard_idle_seconds=0,
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


def _run_supervisor_with_usage(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
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
            f"{run_label}:resume-recovery",
            mission_id,
        )
        messages = list(fresh.agent_messages)
        new_thread_id = fresh.thread_id
        usage = _add_usage_totals(usage, _usage_tuple(fresh))
    return (messages, new_thread_id, usage)


def _run_supervisor(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
) -> tuple[list[str], str | None]:
    """Backward-compatible wrapper returning only messages and thread id."""
    messages, new_thread_id, _usage = _run_supervisor_with_usage(
        prompt,
        model,
        cwd,
        thread_id,
    )
    return messages, new_thread_id


# Backward-compatible aliases for callers/tests importing the old Codex-specific API.
_run_codex_with_usage = _run_supervisor_with_usage
_run_codex = _run_supervisor
