"""Round-loop phase: agent-driven background/external-work cadence waits.

If the entire Engineer turn was a ``WAIT_FOR_SUBAGENT: <id>`` (or the
external-work equivalent) request naming a currently self-watched in-flight
job, this phase skips the expensive checks + Reviewer round for this cycle
and sleeps on that job's own cadence instead, waking early on a terminal
state. This is how the loop honours the background-subagent advisory's "do
not re-poll a self-watched run every round" guidance: the Engineer explicitly
chose to wait, the harness never decides it. A sentinel naming an unknown or
not-self-watched job is ignored and falls through to a normal reviewed round,
so a stale wait request can never hang the mission.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .background_subagents import find_waitable_subagent, parse_wait_sentinel
from .external_work import inspect_external_work, parse_external_wait_sentinel
from .round_signals import _pause_decision_clock
from .round_state import RoundControl, RoundLoopState, control_continue_loop, control_proceed

if TYPE_CHECKING:
    from .runner import SupervisedConfig


class RoundWaitsMixin:
    """Mixin providing ``SupervisedEngineer``'s agent-driven wait phase."""

    def _handle_agent_driven_wait(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        raw_engineer_message: str,
        workdir: Path,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        # Agent-driven cadence yield: if the engineer's entire action this
        # round was a ``WAIT_FOR_SUBAGENT: <id>`` request naming a currently
        # self-watched in-flight subagent, skip the expensive checks+reviewer
        # round and sleep on that subagent's supervisor cadence (waking early
        # if it reaches terminal). This is how the loop honours the advisory's
        # "do not re-poll a self-watched run every round" guidance — the agent
        # explicitly chose to wait, the harness does not decide it. A sentinel
        # naming an unknown / not-self-watched job is ignored (falls through to
        # a normal reviewed round) so a stale request can never hang the loop.
        if supervised_config.background_subagent_advisory:
            wait_task_id = parse_wait_sentinel(raw_engineer_message)
            if wait_task_id and find_waitable_subagent(workdir, wait_task_id) is not None:
                # Call through the ``runner`` module attribute (not a static
                # imported name) so tests that monkeypatch
                # ``runner._run_background_wait`` keep observing it, exactly
                # as when this call lived directly inside ``runner.py``.
                from . import runner as _runner_module

                _, waited_s = _runner_module._run_background_wait(
                    workdir=workdir,
                    task_id=wait_task_id,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    on_event=on_event,
                )
                state.last_decision_progress_at = _pause_decision_clock(
                    state.last_decision_progress_at,
                    waited_s,
                )
                # A deliberate yield is neither progress nor a stall. Preserve
                # the pre-wait streak and re-assess fresh next round.
                return control_continue_loop()

        external_work_id = parse_external_wait_sentinel(raw_engineer_message)
        external_work = (
            inspect_external_work(workdir, external_work_id) if external_work_id else None
        )
        if external_work is not None and external_work.waitable:
            # See the background-wait call above: route through the
            # ``runner`` module attribute so monkeypatching
            # ``runner._run_external_work_wait`` keeps taking effect.
            from . import runner as _runner_module

            _, waited_s = _runner_module._run_external_work_wait(
                workdir=workdir,
                work_id=external_work.work_id,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                on_event=on_event,
            )
            state.last_decision_progress_at = _pause_decision_clock(
                state.last_decision_progress_at,
                waited_s,
            )
            return control_continue_loop()
        return control_proceed()


# Session-tail constants for the effective-progress watchdog's Codex jsonl
# scan (``_EffectiveProgressWatchdog`` in ``runner.py`` calls the helpers
# below by name; moved out verbatim to keep ``runner.py`` under the
# maintainability line-count target).
_CODEX_COMPACTION_EVENT_TYPE = "compacted"
_CODEX_SESSION_EVENT_IGNORED_PAYLOAD_TYPES = {"token_count"}
_PROJECT_PROGRESS_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}


@dataclass(frozen=True)
class _SessionReadResult:
    """Outcome of tailing the new region of one Codex session jsonl."""

    progressed: bool
    compactions: int
    consumed_bytes: int


def _is_project_progress_ignored_dir(parent: Path, name: str) -> bool:
    """Exclude generated trees that can hide real experiment heartbeats.

    Kernel projects commonly use target-specific environments such as
    ``.venv-b200-tilelang`` instead of the exact ``.venv`` name. Walking one of
    those environments can consume the watchdog's bounded file-scan budget
    before it reaches a growing verifier log under ``research/raw``. Detect a
    Python virtual environment by its standard ``pyvenv.cfg`` marker so custom
    names are ignored without relying on an ever-growing name allowlist.
    """
    if name in _PROJECT_PROGRESS_IGNORE_DIRS:
        return True
    try:
        return (parent / name / "pyvenv.cfg").is_file()
    except OSError:
        return False


def _codex_sessions_root() -> Path | None:
    raw_home = os.environ.get("CODEX_HOME")
    root = Path(raw_home).expanduser() if raw_home else Path.home() / ".codex"
    sessions = root / "sessions"
    return sessions if sessions.is_dir() else None


def _session_cwd_matches(path: Path, workdir: Path) -> bool:
    """Return True when a Codex rollout belongs to this project workdir."""
    target = Path(workdir).expanduser().resolve()
    bytes_read = 0
    max_bytes = 256 * 1024
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                bytes_read += len(line.encode("utf-8", errors="ignore"))
                if bytes_read > max_bytes:
                    return False
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if str(event.get("type") or "") != "session_meta":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = event
                raw_cwd = payload.get("cwd")
                if not isinstance(raw_cwd, str) or not raw_cwd.strip():
                    return False
                try:
                    return Path(raw_cwd).expanduser().resolve() == target
                except OSError:
                    return False
    except OSError:
        return False
    return False


def _is_codex_compaction_line(line: str) -> bool:
    """Return True for a top-level Codex auto-compaction session event."""
    text = line.strip()
    if not text:
        return False
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    return str(event.get("type") or "") == _CODEX_COMPACTION_EVENT_TYPE


def _is_effective_codex_session_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    event_type = str(event.get("type") or "")
    if event_type == "token_count":
        return False
    # A compaction is the opposite of progress: it discards context and kicks
    # off a re-read loop. It must never reset the effective-progress timer.
    if event_type == _CODEX_COMPACTION_EVENT_TYPE:
        return False
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type in _CODEX_SESSION_EVENT_IGNORED_PAYLOAD_TYPES:
            return False
    return True
