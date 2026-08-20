"""Agent CLI turns used by setup verification and Doctor repair."""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class AgentProbeResult:
    backend: str
    executable: str
    ok: bool
    output: str = ""
    error: str = ""
    tool_activity_observed: bool = False


def _probe_result(
    result: Any,
    *,
    backend: str,
    executable: str,
    require_tool_activity: bool = False,
) -> AgentProbeResult:
    output = str(getattr(result, "last_agent_message", "") or "").strip()
    if not output:
        messages = list(getattr(result, "agent_messages", None) or ())
        output = next(
            (str(message).strip() for message in reversed(messages) if str(message).strip()),
            "",
        )
    exit_code = int(getattr(result, "exit_code", 1) or 0)
    fatal_error = str(getattr(result, "fatal_error", "") or "").strip()
    turn_completed = getattr(result, "turn_completed", None)
    completion_ok = (
        bool(turn_completed)
        if turn_completed is not None
        else exit_code == 0 and not fatal_error
    )
    tool_activity = bool(getattr(result, "tool_activity_observed", False))
    ok = (
        exit_code == 0
        and completion_ok
        and bool(output)
        and not (require_tool_activity and not tool_activity)
    )
    error = ""
    if not ok:
        if require_tool_activity and not tool_activity:
            error = "Agent returned without inspecting or repairing with tools"
        else:
            error = fatal_error
        if not error:
            stderr = list(getattr(result, "stderr_lines", None) or ())
            error = str(stderr[-1]).strip() if stderr else ""
        if not error:
            error = (
                f"Agent CLI exited {getattr(result, 'exit_code', 'unknown')} "
                "without a completed assistant reply"
            )
    return AgentProbeResult(
        backend=backend,
        executable=executable,
        ok=ok,
        output=output,
        error=error,
        tool_activity_observed=tool_activity,
    )


def run_read_only_agent_prompt(
    *,
    backend: str,
    executable: str,
    prompt: str,
    model: str = "",
    run_label: str,
) -> AgentProbeResult:
    """Run one real Agent CLI turn in a read-only sandbox."""
    from ..adapters.agent_cli_backend import AgentCliBackend
    from .models import RunnerOptions
    from .run_gateway import run_exec

    try:
        with tempfile.TemporaryDirectory(prefix="argus-agent-probe-") as workdir:
            runner = AgentCliBackend(
                backend=backend,
                runner_bin=executable,
                default_watchdog_soft_idle_seconds=15,
                default_watchdog_stalled_idle_seconds=45,
                default_watchdog_hard_idle_seconds=90,
            )
            result = run_exec(
                runner,
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=model or None,
                    working_dir=workdir,
                    sandbox_mode="read-only",
                    force_safe_mode=True,
                    skip_git_repo_check=True,
                ),
                run_label=run_label,
            )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return AgentProbeResult(
            backend=backend,
            executable=executable,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    return _probe_result(
        result,
        backend=backend,
        executable=executable,
    )


def run_agent_repair_prompt(
    *,
    backend: str,
    executable: str,
    prompt: str,
    working_dir: Path,
    add_dirs: Sequence[Path] = (),
    known_secret_values: Sequence[str] = (),
    model: str = "",
    run_label: str = "doctor-repair",
) -> AgentProbeResult:
    """Run one installed Agent with tools enabled so it can repair Argus."""
    from ..adapters.agent_cli_backend import AgentCliBackend
    from .models import RunnerOptions
    from .run_gateway import run_exec

    try:
        runner = AgentCliBackend(
            backend=backend,
            runner_bin=executable,
            default_watchdog_soft_idle_seconds=30,
            default_watchdog_stalled_idle_seconds=120,
            default_watchdog_hard_idle_seconds=600,
            known_secret_values_override=known_secret_values,
        )
        result = run_exec(
            runner,
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=model or None,
                working_dir=str(working_dir),
                add_dirs=[str(path) for path in add_dirs] or None,
                dangerous_yolo=True,
                full_auto=True,
                skip_git_repo_check=True,
            ),
            run_label=run_label,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return AgentProbeResult(
            backend=backend,
            executable=executable,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return _probe_result(
        result,
        backend=backend,
        executable=executable,
        require_tool_activity=True,
    )


__all__ = [
    "AgentProbeResult",
    "run_agent_repair_prompt",
    "run_read_only_agent_prompt",
]
