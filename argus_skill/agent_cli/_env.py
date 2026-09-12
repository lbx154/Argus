"""Environment-driven tuning knobs shared by the runner's exec/stream path.

Pure functions and env-var name constants only — no behavior lives here beyond
parsing ``os.environ``, so this module has no side effects and no dependency on
the rest of the runner.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

from ..core.secret_guard import redact_secrets_text

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
# An operator can opt into per-call context rotation. By default there is no
# fixed interaction-count ceiling: Engineer finishes a meaningful increment
# and the independent Reviewer decides whether the task is complete.
_PROVIDER_TURN_CAP_ENV = "ARGUS_SKILL_PROVIDER_TURN_CAP"
_DEFAULT_PROVIDER_TURN_CAP = 0
# The wind-down call ("write the checkpoint, reply with a summary") resumes the
# very conversation that just used its whole allowance, so it gets only a small
# allowance of its own.
_WIND_DOWN_PROVIDER_TURN_ALLOWANCE = 8
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


def _provider_turn_cap(run_label: str | None) -> int:
    """Per-call provider-turn allowance for this run label; 0 = no allowance.

    Applies only to Engineer and Reviewer calls (``engineer-r3``, ``reviewer``,
    ``reviewer-cold-read``, …): those are the two roles whose calls run long
    tool conversations, and the supervised round loop knows how to continue
    them in a fresh session. Control-plane labels (manager/planner/subagent)
    keep their existing bounds and are never cut here.
    """
    label = str(run_label or "").strip().lower()
    if not label.startswith(("engineer", "reviewer")):
        return 0
    cap = _nonnegative_env_int(_PROVIDER_TURN_CAP_ENV, _DEFAULT_PROVIDER_TURN_CAP)
    if cap <= 0:
        return 0
    if label.endswith(".winddown"):
        return min(cap, _WIND_DOWN_PROVIDER_TURN_ALLOWANCE)
    return cap


# The last lines of a CLI's stderr are usually where the actual reason for a
# failed exit is written ("connect ECONNREFUSED 127.0.0.1:18765", "Failed to
# load models"); without them all anyone sees is the exit code. The tail is
# bounded so it stays readable inside a failure record, terminal colour and
# control sequences are dropped, credentials are redacted, and local paths are
# kept on purpose: they are what makes a crash actionable.
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 2048
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor movement
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: titles, hyperlinks
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLI_EXITED_WITHOUT_TURN = "Agent CLI exited without completing a model turn."


def stderr_tail(stderr_lines: Iterable[str | bytes]) -> str:
    """The last lines of a CLI's stderr as clean, redacted, valid UTF-8 text.

    Empty when the CLI printed nothing worth showing. Blank lines inside the
    tail are kept so the text reads as the CLI wrote it.
    """
    lines: list[str] = []
    for raw in stderr_lines:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        lines.append(_CONTROL_CHAR_RE.sub("", _ANSI_ESCAPE_RE.sub("", text)).rstrip())
    while lines and not lines[-1]:
        lines.pop()
    tail = "\n".join(lines[-_STDERR_TAIL_LINES:]).strip()
    if len(tail) > _STDERR_TAIL_CHARS:
        tail = tail[-_STDERR_TAIL_CHARS:]
        cut = tail.find("\n")
        if 0 <= cut < len(tail) - 1:
            tail = tail[cut + 1:]
    tail = tail.encode("utf-8", errors="replace").decode("utf-8")
    return redact_secrets_text(tail)


def _incomplete_turn_error(
    stderr_lines: Iterable[str | bytes],
    *,
    receipt: str = "",
    log_hint: str = "",
) -> str:
    """Best available diagnostic for a CLI that exited without a model turn.

    ``receipt`` is the runner's own one-line account of how the call ended
    ("Copilot CLI exited with code 1."). The CLI's last stderr lines follow it,
    because that is where the reason usually is. When the CLI printed nothing,
    the record says so and, if ``log_hint`` names the CLI's own log directory,
    where to look instead.
    """
    tail = stderr_tail(stderr_lines)
    receipt = str(receipt or "").strip()
    if receipt and not receipt.endswith((".", "!", "?")):
        receipt += "."
    if tail and receipt:
        return f"{receipt}\n{tail}"
    if tail:
        return tail
    if receipt:
        where = f"; its own log is under {log_hint}" if log_hint else ""
        return f"{receipt} It printed nothing on stderr{where}."
    return _CLI_EXITED_WITHOUT_TURN
