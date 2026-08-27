"""Environment-driven tuning knobs shared by the runner's exec/stream path.

Pure functions and env-var name constants only — no behavior lives here beyond
parsing ``os.environ``, so this module has no side effects and no dependency on
the rest of the runner.
"""
from __future__ import annotations

import os

_CAPTURE_STDOUT_LINES_ENV = "ARGUS_SKILL_RUNNER_CAPTURE_STDOUT_LINES"
_CAPTURE_STDERR_LINES_ENV = "ARGUS_SKILL_RUNNER_CAPTURE_STDERR_LINES"
_CAPTURE_JSON_EVENTS_ENV = "ARGUS_SKILL_RUNNER_CAPTURE_JSON_EVENTS"
_STREAM_QUEUE_LINES_ENV = "ARGUS_SKILL_RUNNER_STREAM_QUEUE_LINES"
# These deques bound RAM; complete provider output is persisted in agent I/O logs.
_DEFAULT_CAPTURE_STDOUT_LINES = 512
_DEFAULT_CAPTURE_STDERR_LINES = 256
_DEFAULT_CAPTURE_JSON_EVENTS = 2048
_DEFAULT_STREAM_QUEUE_LINES = 4096
_ENGINEER_TURN_MAX_SECONDS_ENV = "ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS"
_DEFAULT_ENGINEER_TURN_MAX_SECONDS = 0
_SCIENTIST_TURN_MAX_SECONDS_ENV = "ARGUS_SKILL_SCIENTIST_TURN_MAX_SECONDS"
_DEFAULT_SCIENTIST_TURN_MAX_SECONDS = 0
_MANAGER_TURN_MAX_SECONDS_ENV = "ARGUS_SKILL_MANAGER_TURN_MAX_SECONDS"
_DEFAULT_MANAGER_TURN_MAX_SECONDS = 0
# These labels sit inside the synchronous Manager request even though they use
# older or role-specific names. An operator may still give them an explicit cap.
_SYNCHRONOUS_MANAGER_TURN_LABELS = frozenset(
    {
        "chat-1",
        "router-classify",
        "self-debug",
        "self-implement",
        "self-micro",
        "self-review",
        "self-synthesize",
        "simple-1",
    }
)


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _nonnegative_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _is_manager_turn_label(run_label: str | None) -> bool:
    label = str(run_label or "").strip().lower()
    return (
        label.startswith(("manager-", "manager."))
        or label in _SYNCHRONOUS_MANAGER_TURN_LABELS
    )


def _turn_wall_clock_seconds(run_label: str | None) -> int:
    label = str(run_label or "").strip().lower()
    if _is_manager_turn_label(label):
        return _nonnegative_env_int(
            _MANAGER_TURN_MAX_SECONDS_ENV,
            _DEFAULT_MANAGER_TURN_MAX_SECONDS,
        )
    if label == "scientist.skill_distill":
        return _nonnegative_env_int(
            _SCIENTIST_TURN_MAX_SECONDS_ENV,
            _DEFAULT_SCIENTIST_TURN_MAX_SECONDS,
        )
    if not (label.startswith("engineer") or label == "main"):
        return 0
    return _nonnegative_env_int(
        _ENGINEER_TURN_MAX_SECONDS_ENV,
        _DEFAULT_ENGINEER_TURN_MAX_SECONDS,
    )


def _incomplete_turn_error(stderr_lines: list[str]) -> str:
    """Best available diagnostic for a CLI that exited without a model turn."""
    nonempty = [line.strip() for line in stderr_lines if line.strip()]
    for line in reversed(nonempty):
        if line.casefold().startswith(("error:", "fatal:")):
            return line
    if nonempty:
        return nonempty[-1]
    return "Agent CLI exited without completing a model turn."
