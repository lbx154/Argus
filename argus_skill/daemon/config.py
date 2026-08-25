"""Daemon worker configuration and handoff serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LifeWorkerConfig:
    """How the worker drains the backlog.

    All durations are seconds. ``poll_interval`` is how long the worker
    sleeps between :meth:`LifeSupervisor.tick` calls when the backlog
    is empty — when work is pending it does not sleep, it just keeps
    ticking.
    """

    life_dir: Path
    global_root: Path | None = None
    project_fingerprint: str = ""
    project_label: str = ""
    backend: str = "codex"  # "codex" | "memory"
    engineer_model: str = "gpt-5.5"
    reviewer_model: str = "gpt-5.5"
    engineer_reasoning_effort: str = "xhigh"
    reviewer_reasoning_effort: str = "high"
    global_daily_cap_usd: float = 0.0
    mission_width: int = 2
    planner_task_iteration_max_cycles: int = 6
    # See LifeSupervisorConfig.subagent_family_failure_streak_limit /
    # ..._window_hours (life/supervisor/_config.py) for the circuit breaker
    # this configures.
    subagent_family_failure_streak_limit: int = 3
    subagent_family_failure_window_hours: float = 72.0
    poll_interval: float = 5.0
    log_path: Path | None = None  # defaults to <life_dir>/daemon.log
    project_workdir: Path | None = None
    # Persisted-events verbosity for the daemon's own events.jsonl. DEFAULT
    # "full": the cockpit tails this file, so dropping engineer.progress
    # / round.review.* would break streaming (and hide the reviewer working).
    # events.jsonl is size-bounded by JsonlEventSink's roll. "signal" is an
    # opt-in (ARGUS_SKILL_EVENT_VERBOSITY) for a tiny verdict-only log; the
    # display noise is solved at the presentation layer, not by gutting the log.
    event_log_verbosity: str = "full"
    continuous: bool = False
    continuous_objective: str = ""
    continuous_objective_file: Path | None = None
    # Opt-in to adopt THIS project's persisted continuous campaign
    # (``<life_dir>/continuous.json``) at boot. Off by default: a fresh/manual
    # daemon must NOT silently inherit a project-level campaign it was not asked
    # to run (see ``--resume-continuous``). Supervisors that restart the campaign
    # daemon pass it True to preserve crash-recovery.
    resume_continuous: bool = False
    # When True (the default for the lifetime daemon) the supervisor keeps the
    # mission alive after the planner certifies ``project_done`` instead of
    # hard-stopping. Set False (via ``--bounded``) for a one-shot bounded goal.
    continuous_open_ended: bool = True
    # Parent-only diagnostic populated by the clean launcher.  It is
    # deliberately excluded from config_payload(): the child does not need to
    # receive its own launch failure, while WebAPI needs the captured helper
    # stderr after the integer return code comes back.
    last_spawn_error: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.mission_width = int(self.mission_width)
        if self.mission_width < 0:
            raise ValueError("mission_width must be zero or a positive integer")

def config_payload(config: LifeWorkerConfig) -> dict[str, Any]:
    return {
        "life_dir": str(config.life_dir),
        "global_root": str(config.global_root) if config.global_root is not None else "",
        "project_fingerprint": config.project_fingerprint,
        "project_label": config.project_label,
        "backend": config.backend,
        "engineer_model": config.engineer_model,
        "reviewer_model": config.reviewer_model,
        "engineer_reasoning_effort": config.engineer_reasoning_effort,
        "reviewer_reasoning_effort": config.reviewer_reasoning_effort,
        "global_daily_cap_usd": config.global_daily_cap_usd,
        "mission_width": config.mission_width,
        "planner_task_iteration_max_cycles": config.planner_task_iteration_max_cycles,
        "subagent_family_failure_streak_limit": config.subagent_family_failure_streak_limit,
        "subagent_family_failure_window_hours": config.subagent_family_failure_window_hours,
        "poll_interval": config.poll_interval,
        "log_path": str(config.log_path) if config.log_path is not None else "",
        "project_workdir": str(config.project_workdir) if config.project_workdir is not None else "",
        "continuous": config.continuous,
        "continuous_objective": config.continuous_objective,
        "continuous_objective_file": (
            str(config.continuous_objective_file)
            if config.continuous_objective_file is not None
            else ""
        ),
        "resume_continuous": config.resume_continuous,
        "continuous_open_ended": config.continuous_open_ended,
    }


def config_from_payload(data: dict[str, Any]) -> LifeWorkerConfig:
    from ..core.knobs import resolve_role_model

    log_path = str(data.get("log_path") or "")
    global_root = str(data.get("global_root") or "")
    project_workdir = str(data.get("project_workdir") or "")
    continuous_objective_file = str(data.get("continuous_objective_file") or "")
    backend = str(data.get("backend") or "codex")

    def _model(name: str, route: str, role_env: str) -> str:
        if name in data:
            return str(data.get(name) or "")
        return resolve_role_model(route, role_env=role_env, backend=backend)

    def _number(name: str, default: float) -> float:
        value = data.get(name)
        return default if value is None else float(value)

    return LifeWorkerConfig(
        life_dir=Path(str(data["life_dir"])).expanduser(),
        global_root=Path(global_root).expanduser() if global_root else None,
        project_workdir=Path(project_workdir).expanduser() if project_workdir else None,
        project_fingerprint=str(data.get("project_fingerprint") or ""),
        project_label=str(data.get("project_label") or ""),
        backend=backend,
        engineer_model=_model(
            "engineer_model",
            "engineer",
            "ARGUS_SKILL_ENGINEER_MODEL",
        ),
        reviewer_model=_model(
            "reviewer_model",
            "reviewer",
            "ARGUS_SKILL_REVIEWER_MODEL",
        ),
        engineer_reasoning_effort=str(
            data.get("engineer_reasoning_effort") or "xhigh"
        ),
        reviewer_reasoning_effort=str(
            data.get("reviewer_reasoning_effort") or "high"
        ),
        global_daily_cap_usd=_number("global_daily_cap_usd", 30.0),
        mission_width=int(data.get("mission_width", 2)),
        planner_task_iteration_max_cycles=int(
            data.get("planner_task_iteration_max_cycles") or 6
        ),
        subagent_family_failure_streak_limit=int(
            data.get("subagent_family_failure_streak_limit") or 3
        ),
        subagent_family_failure_window_hours=float(
            data.get("subagent_family_failure_window_hours") or 72.0
        ),
        poll_interval=float(data.get("poll_interval") or 5.0),
        log_path=Path(log_path).expanduser() if log_path else None,
        continuous=bool(data.get("continuous")),
        continuous_objective=str(data.get("continuous_objective") or ""),
        continuous_objective_file=(
            Path(continuous_objective_file).expanduser()
            if continuous_objective_file
            else None
        ),
        resume_continuous=bool(data.get("resume_continuous")),
        continuous_open_ended=bool(data.get("continuous_open_ended", True)),
    )

__all__ = ["LifeWorkerConfig", "config_from_payload", "config_payload"]
