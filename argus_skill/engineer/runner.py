"""SupervisedEngineer: round-loop wrapper around an engineer call.

This is the heart of the argus-skill v0.1 integration:

  * Each round, run the engineer with the current task prompt
    (initial task + optional skill block + optional reviewer next_action
    from prior round).
  * Run the user-provided acceptance checks (shell commands).
  * Call the reviewer to render a structured verdict.
  * If ``done``, stop. If ``continue``, capture ``next_action`` and loop.
    If ``blocked``, stop and surface the reason.

Provenance: the round-loop control flow is adapted from
``ArgusBot/codex_autoloop/core/engine.py`` (LoopEngine), simplified to the
single-agent case — argus-skill does not have ArgusBot's planner /
explore subagent; the skill block plays a similar "what to do" role for
the engineer in front of you.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Protocol

from ..core.models import (
    CheckResult,
    LoopOutcome,
    LoopStatus,
    ReviewDecision,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from .checkpoint import CheckpointState, load_checkpoint, save_checkpoint
from .checks import all_checks_passed, run_checks
from .reviewer import Reviewer, ReviewerConfig

log = logging.getLogger(__name__)

_POISONED_SESSION_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "empty output",
    "empty-output",
    "no output",
    "no-output",
    "out of room",
    "context window",
    "clear earlier history",
    "start a new thread",
    "start new thread",
    "no rollout found for thread id",
)


_BACKEND_FAILURE_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "too many requests",
    "429",
    "rate limit",
    "rate-limit",
    "forced restart after hard idle timeout",
    "hard idle timeout",
    "service unavailable",
    "gateway timeout",
    "bad gateway",
    "connection reset",
    "connection closed",
    "connection aborted",
    "network error",
)

_EFFECTIVE_PROGRESS_TIMEOUT_MARKER = "effective progress timeout"
# Distinct marker for the in-round auto-compaction amnesia loop (a busy-but-
# unproductive churn, not a silent stall). Kept separate from the timeout
# marker so fatal-error metrics/searches don't conflate the two failure modes,
# while still reusing the same recoverable between-round handling.
_COMPACTION_THRASH_MARKER = "compaction thrash"
_RECOVERABLE_RECONNECT_RE = re.compile(r"^reconnecting\.\.\.\s*(\d+)/(\d+)\b")
_DAEMON_STOP_INTERRUPT_RE = re.compile(r"^external interrupt:\s*daemon stop requested\b")

_EFFECTIVE_PROGRESS_TIMEOUT_ENV = "ARGUS_SKILL_EFFECTIVE_PROGRESS_TIMEOUT_SECONDS"
_EFFECTIVE_PROGRESS_CHECK_INTERVAL_ENV = (
    "ARGUS_SKILL_EFFECTIVE_PROGRESS_CHECK_INTERVAL_SECONDS"
)
_RUNNER_HARD_IDLE_ENV = "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS"
_SHIFT_ROUND_LIMIT_ENV = "ARGUS_SKILL_SHIFT_ROUND_LIMIT"
_THREAD_TOKEN_LIMIT_ENV = "ARGUS_SKILL_THREAD_TOKEN_LIMIT"
_ROUND_COMPACTION_LIMIT_ENV = "ARGUS_SKILL_ROUND_COMPACTION_LIMIT"
# Coarse upper bound on the input-token size a single resumed Codex thread may
# reach before it is rolled. A healthy fresh round is ~0.7M and a couple of
# legitimate work rounds reach ~2M; the amnesia/re-read loop lived at 5-7M
# where codex's lossy auto-compaction kicks in. 4M sits between normal
# operation and the bloat zone. Tunable via ARGUS_SKILL_THREAD_TOKEN_LIMIT
# (0 disables the token roll).
_DEFAULT_THREAD_TOKEN_LIMIT = 4_000_000
_EFFECTIVE_PROGRESS_DEFAULT_TIMEOUT_SECONDS = 60 * 60
# A single engineer round should essentially never reach Codex auto-compaction:
# the runner proactively rolls the session every ``shift_round_limit`` rounds
# and at ``thread_token_limit`` input tokens precisely to stay below it. So a
# handful of ``compacted`` events *within one round* means that anti-amnesia
# design has already been defeated and the round is in the re-read/re-emit
# amnesia loop. We keep the default at 3 (not 1) to tolerate an occasional
# benign compaction. Set ARGUS_SKILL_ROUND_COMPACTION_LIMIT=0 to disable.
_DEFAULT_ROUND_COMPACTION_LIMIT = 3
_EFFECTIVE_PROGRESS_DEFAULT_CHECK_INTERVAL_SECONDS = 30.0
_EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS = 120.0
_RUNNER_DEFAULT_HARD_IDLE_SECONDS = 60 * 60
_CODEX_SESSION_EVENT_IGNORED_PAYLOAD_TYPES = {"token_count"}
# Top-level Codex session event type written when the agent auto-compacts its
# context. A compaction is the *opposite* of progress (it discards context and
# triggers a re-read loop), so it never counts as effective progress and is
# tallied separately to detect the in-round amnesia thrash.
_CODEX_COMPACTION_EVENT_TYPE = "compacted"
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
_PROJECT_PROGRESS_MAX_FILES = 5000
_PROJECT_PROGRESS_SCAN_BUDGET_SECONDS = 0.35

_SUCCESS_ITEM_STATUSES: tuple[str, ...] = (
    "completed",
    "succeeded",
    "success",
    "ok",
    "applied",
)
_FAILED_ITEM_STATUSES: tuple[str, ...] = (
    "failed",
    "error",
    "cancelled",
    "canceled",
)


def _fatal_error_looks_like_poisoned_session(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _POISONED_SESSION_FATAL_ERROR_PATTERNS)


def fatal_error_looks_like_backend_failure(fatal_error: str | None) -> bool:
    """Return True for Codex/backend transport failures only.

    The match is intentionally restricted to ``RunnerResult.fatal_error``;
    do not call this on model prose, check output, or command stderr.
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    if fatal_error_looks_like_recoverable_reconnect(fatal_error):
        return False
    return any(pattern in low for pattern in _BACKEND_FAILURE_FATAL_ERROR_PATTERNS)


def fatal_error_looks_like_recoverable_reconnect(fatal_error: str | None) -> bool:
    """Return True for Codex CLI reconnect progress notices.

    Codex emits messages such as
    ``Reconnecting... 1/100 (stream disconnected before completion: ...)``.
    The CLI can keep recovering after high attempt counts, so Argus must not
    convert the notice into its own backend-failure state.
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    match = _RECOVERABLE_RECONNECT_RE.search(low)
    return bool(match)


def fatal_error_looks_like_effective_progress_timeout(fatal_error: str | None) -> bool:
    """Return True when the semantic-progress watchdog stopped a stale turn."""
    if not fatal_error:
        return False
    return _EFFECTIVE_PROGRESS_TIMEOUT_MARKER in str(fatal_error).strip().casefold()


def fatal_error_looks_like_compaction_thrash(fatal_error: str | None) -> bool:
    """Return True when the watchdog stopped an in-round auto-compaction loop."""
    if not fatal_error:
        return False
    return _COMPACTION_THRASH_MARKER in str(fatal_error).strip().casefold()


def fatal_error_looks_like_daemon_stop_request(fatal_error: str | None) -> bool:
    """Return True for intentional daemon shutdown interrupts."""
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return bool(_DAEMON_STOP_INTERRUPT_RE.search(low))


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _review_event_payload(
    review: ReviewDecision,
    *,
    round_index: int,
    round_max: int,
    text: str,
    review_skipped: bool = False,
) -> dict[str, object]:
    """Adapter — runner adds ``round_max`` / ``text`` / ``review_skipped``
    on top of the canonical reviewer payload. The reviewer JSON schema's
    full field set lives in ``ReviewDecision.to_event_payload``; this
    keeps engineer-runner and mission-engine emit sites consistent."""
    return review.to_event_payload(
        round_index=round_index,
        round_max=round_max,
        text=text,
        review_skipped=review_skipped,
    )


def should_clear_thread_id_after_outcome(*, status: str, fatal_error: str | None) -> bool:
    """Return True when the carried Codex thread id should be cleared."""
    return (
        str(status).strip().casefold() == "no_progress"
        or _fatal_error_looks_like_poisoned_session(fatal_error)
        or fatal_error_looks_like_effective_progress_timeout(fatal_error)
        or fatal_error_looks_like_compaction_thrash(fatal_error)
        or fatal_error_looks_like_backend_failure(fatal_error)
    )


def _runner_result_has_successful_work_signal(
    result: RunnerResult,
    *,
    engineer_message: str,
) -> bool:
    if fatal_error_looks_like_effective_progress_timeout(
        getattr(result, "fatal_error", None)
    ):
        return False
    if fatal_error_looks_like_compaction_thrash(
        getattr(result, "fatal_error", None)
    ):
        return False
    if engineer_message.strip():
        return True
    if fatal_error_looks_like_backend_failure(getattr(result, "fatal_error", None)):
        return False

    for raw in getattr(result, "stdout_lines", []) or []:
        event = _parse_json_event(raw)
        if event is not None and _event_has_successful_work_signal(event):
            return True
    return False


def _parse_json_event(raw: object) -> dict | None:
    text = str(raw or "").strip()
    if not text or text[0] not in "{[":
        return None
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def _event_has_successful_work_signal(event: dict) -> bool:
    event_type = str(event.get("type") or "").strip()
    if event_type == "item.completed":
        item = event.get("item") or {}
        if not isinstance(item, dict):
            return False
        kind = str(item.get("type") or "").strip()
        status = str(item.get("status") or "").strip().casefold()
        exit_code = item.get("exit_code")
        if kind == "agent_message":
            return bool(str(item.get("text") or "").strip())
        if status in _FAILED_ITEM_STATUSES:
            return False
        if kind == "command_execution":
            return exit_code == 0 or status in _SUCCESS_ITEM_STATUSES
        if kind in {"file_change", "tool_use"}:
            return status in _SUCCESS_ITEM_STATUSES or bool(item.get("changes"))
        return False
    if event_type in {"tool.result", "assistant.message"}:
        data = event.get("data") or {}
        if isinstance(data, dict):
            return bool(str(data.get("content") or data.get("output") or "").strip())
    return False


@dataclass
class EngineerConfig:
    model: str
    reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False


@dataclass
class SupervisedConfig:
    """Knobs for the round-loop control."""
    max_rounds: int = 500
    check_commands: list[str] = field(default_factory=list)
    check_timeout_seconds: int = 600
    no_progress_threshold: int = 2  # consecutive rounds with no engineer message before bailing
    # Consecutive ``continue`` rounds where the reviewer EXPLICITLY reports
    # ``forward_progress == false`` (engineer is active but the reviewer judges
    # the project did not actually advance) before bailing as ``no_progress``.
    # This is the SEMANTIC stall guard, distinct from ``no_progress_threshold``
    # which only counts rounds with no engineer output at all. Only an explicit
    # boolean ``false`` counts — a missing/omitted field never does — so a
    # reviewer/schema hiccup can never falsely kill a healthy long mission.
    # Set high so it catches genuine runaway spins (the observed pathology was
    # 17–19 fruitless rounds) while leaving ample room for legitimate staged
    # setup / status-polling rounds. 0 disables it.
    stall_threshold: int = 8
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    session_id: str | None = None
    # Curated-memory checkpoint: how many rounds a single Codex thread may live
    # before it is proactively rolled (dropped) so the next round starts a
    # fresh session seeded only by the checkpoint. Bounds per-session context
    # growth to prevent the repeated auto-compaction amnesia loop. 0 disables
    # the proactive roll (e.g. tests / interactive chat). Env override:
    # ARGUS_SKILL_SHIFT_ROUND_LIMIT.
    shift_round_limit: int = field(
        default_factory=lambda: _env_int(_SHIFT_ROUND_LIMIT_ENV, 8)
    )
    # Cross-mission context bound. The Codex thread is resumed across (often
    # short) missions, so the per-mission ``shift_round_limit`` counter resets
    # before it can fire and the thread grows unbounded until codex performs a
    # lossy auto-compaction (the amnesia/re-read loop). This caps the thread by
    # the previous round's reported input-token count instead of round count,
    # so an inherited bloated thread is dropped on its first round. 0 disables.
    # Env override: ARGUS_SKILL_THREAD_TOKEN_LIMIT.
    thread_token_limit: int = field(
        default_factory=lambda: _env_int(
            _THREAD_TOKEN_LIMIT_ENV, _DEFAULT_THREAD_TOKEN_LIMIT
        )
    )
    # Where to persist the curated checkpoint (cross-mission / crash
    # continuity). None = in-memory only for this mission.
    checkpoint_path: Path | None = None
    # Kill a live Codex subprocess if it keeps emitting heartbeat/token
    # noise but makes no effective progress for a long time. Effective
    # progress means either a non-token Codex session event or a project file
    # change. The default is intentionally long because paper/research turns
    # often spend many minutes reading, planning, or waiting on model-side
    # recovery before the next file write.
    # Set ARGUS_SKILL_EFFECTIVE_PROGRESS_TIMEOUT_SECONDS=0 to disable.
    effective_progress_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            _EFFECTIVE_PROGRESS_TIMEOUT_ENV,
            _EFFECTIVE_PROGRESS_DEFAULT_TIMEOUT_SECONDS,
        )
    )
    effective_progress_check_interval_seconds: float = field(
        default_factory=lambda: _env_float(
            _EFFECTIVE_PROGRESS_CHECK_INTERVAL_ENV,
            _EFFECTIVE_PROGRESS_DEFAULT_CHECK_INTERVAL_SECONDS,
            minimum=1.0,
        )
    )
    runner_hard_idle_seconds: int = field(
        default_factory=lambda: _env_int(
            _RUNNER_HARD_IDLE_ENV,
            _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        )
    )
    # Interrupt a round once its Codex session auto-compacts this many times
    # within the single round (the in-round amnesia-loop signature). Set
    # ARGUS_SKILL_ROUND_COMPACTION_LIMIT=0 to disable.
    round_compaction_limit: int = field(
        default_factory=lambda: _env_int(
            _ROUND_COMPACTION_LIMIT_ENV,
            _DEFAULT_ROUND_COMPACTION_LIMIT,
        )
    )


class _AdvisoryLedger(Protocol):
    def render_advisory(self) -> str: ...


@dataclass(frozen=True)
class _SessionReadResult:
    """Outcome of tailing the new region of one Codex session jsonl."""

    progressed: bool
    compactions: int
    consumed_bytes: int


class _EffectiveProgressWatchdog:
    """Detect live-but-stale Codex turns.

    Codex can keep a subprocess alive by writing token-count heartbeats
    after the last real tool/message event. The lower-level idle watcher
    sees those heartbeats as stdout activity, so this watchdog looks at
    semantic progress instead.
    """

    def __init__(
        self,
        *,
        workdir: Path,
        timeout_seconds: int,
        check_interval_seconds: float,
        on_event: Callable[[dict], None] | None = None,
        run_label: str | None = None,
        compaction_limit: int = 0,
        now: float | None = None,
    ) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.timeout_seconds = max(0, int(timeout_seconds or 0))
        self.check_interval_seconds = max(1.0, float(check_interval_seconds or 1.0))
        self.on_event = on_event
        self.run_label = run_label
        self.compaction_limit = max(0, int(compaction_limit or 0))
        self.started_at = time.time() if now is None else float(now)
        self.last_effective_progress_at = self.started_at
        self._last_check_at = 0.0
        self._interrupt_reason: str | None = None
        self._interrupted_event_sent = False
        self._compaction_thrash_event_sent = False
        self._compaction_count = 0
        self._last_waiting_event_at = 0.0
        self._project_signature = self._latest_project_signature()
        self._session_root = _codex_sessions_root()
        self._session_offsets = self._initial_session_offsets()
        self._relevant_sessions: set[Path] = set()

    def interrupt_reason(self) -> str | None:
        if self.timeout_seconds <= 0:
            return None
        if self._interrupt_reason:
            return self._interrupt_reason
        now = time.monotonic()
        if now - self._last_check_at < self.check_interval_seconds:
            return None
        self._last_check_at = now
        try:
            self._refresh_effective_progress()
        except Exception:  # noqa: BLE001 - watchdog must never crash a runner
            log.debug("effective progress watchdog check failed", exc_info=True)
            return None

        if (
            self.compaction_limit
            and self._compaction_count >= self.compaction_limit
        ):
            self._interrupt_reason = (
                "compaction thrash: codex auto-compaction amnesia loop — "
                f"{self._compaction_count} compactions within one round "
                f"(limit {self.compaction_limit}); rolling to a fresh "
                "checkpoint-seeded session"
            )
            self._emit_compaction_thrash_event()
            return self._interrupt_reason

        idle_seconds = time.time() - self.last_effective_progress_at
        if idle_seconds < self.timeout_seconds:
            self._emit_waiting_event(idle_seconds)
            return None

        self._interrupt_reason = (
            "effective progress timeout: no non-token Codex session events "
            f"or project file changes for {int(idle_seconds)}s "
            f"(limit {self.timeout_seconds}s)"
        )
        self._emit_interrupted_event(idle_seconds)
        return self._interrupt_reason

    def current_interrupt_reason(self) -> str | None:
        return self._interrupt_reason

    def _refresh_effective_progress(self) -> None:
        if self._project_changed():
            self._mark_effective_progress()
        if self._session_progressed():
            self._mark_effective_progress()

    def _mark_effective_progress(self) -> None:
        self.last_effective_progress_at = time.time()

    def _emit_interrupted_event(self, idle_seconds: float) -> None:
        if self._interrupted_event_sent or self.on_event is None:
            return
        self._interrupted_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.effective_progress_timeout",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog event failed", exc_info=True)

    def _emit_compaction_thrash_event(self) -> None:
        if self._compaction_thrash_event_sent or self.on_event is None:
            return
        self._compaction_thrash_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.compaction_thrash",
                "run_label": self.run_label,
                "compaction_count": int(self._compaction_count),
                "limit": int(self.compaction_limit),
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("compaction thrash watchdog event failed", exc_info=True)

    def _emit_waiting_event(self, idle_seconds: float) -> None:
        if self.on_event is None:
            return
        if idle_seconds < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS:
            return
        now = time.time()
        if (
            self._last_waiting_event_at
            and now - self._last_waiting_event_at
            < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS
        ):
            return
        self._last_waiting_event_at = now
        try:
            self.on_event({
                "type": "round.watchdog.waiting",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "text": (
                    "engineer turn is still alive but has no non-token Codex "
                    f"session events or project file changes for {int(idle_seconds)}s "
                    f"(limit {self.timeout_seconds}s)"
                ),
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog waiting event failed", exc_info=True)

    def _project_changed(self) -> bool:
        signature = self._latest_project_signature()
        if signature is None:
            return False
        previous = self._project_signature
        self._project_signature = signature
        if previous is None:
            return signature[0] >= self.started_at - 1.0
        return signature != previous and signature[0] >= self.started_at - 1.0

    def _latest_project_signature(self) -> tuple[float, int, str] | None:
        started = time.monotonic()
        newest: tuple[float, int, str] | None = None
        scanned = 0
        try:
            walker = os.walk(self.workdir, topdown=True)
        except OSError:
            return None
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                name for name in dirnames
                if name not in _PROJECT_PROGRESS_IGNORE_DIRS
            ]
            for filename in filenames:
                if scanned >= _PROJECT_PROGRESS_MAX_FILES:
                    return newest
                if time.monotonic() - started > _PROJECT_PROGRESS_SCAN_BUDGET_SECONDS:
                    return newest
                path = Path(dirpath) / filename
                scanned += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                candidate = (float(stat.st_mtime), int(stat.st_size), str(path))
                if newest is None or candidate > newest:
                    newest = candidate
        return newest

    def _initial_session_offsets(self) -> dict[Path, int]:
        offsets: dict[Path, int] = {}
        root = self._session_root
        if root is None:
            return offsets
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return offsets
        for path in paths:
            try:
                offsets[path] = path.stat().st_size
            except OSError:
                continue
        return offsets

    def _session_progressed(self) -> bool:
        root = self._session_root
        if root is None:
            return False
        progressed = False
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return False
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            previous_offset = self._session_offsets.get(path)
            if previous_offset is None:
                previous_offset = 0
            if stat.st_size < previous_offset:
                previous_offset = 0
            if stat.st_size == previous_offset:
                self._session_offsets[path] = stat.st_size
                continue
            if not self._session_relevant(path):
                self._session_offsets[path] = stat.st_size
                continue
            result = self._read_effective_session_events(path, previous_offset)
            if result.progressed:
                progressed = True
            if result.compactions:
                self._compaction_count += result.compactions
            # Advance only past fully newline-terminated lines so a partially
            # written trailing line is re-read (and its eventual ``compacted``
            # event is never silently skipped) on the next poll.
            self._session_offsets[path] = previous_offset + result.consumed_bytes
        return progressed

    def _session_relevant(self, path: Path) -> bool:
        if path in self._relevant_sessions:
            return True
        if _session_cwd_matches(path, self.workdir):
            self._relevant_sessions.add(path)
            return True
        return False

    def _read_effective_session_events(
        self, path: Path, offset: int
    ) -> _SessionReadResult:
        try:
            with path.open("rb") as fh:
                fh.seek(max(0, offset))
                data = fh.read()
        except OSError:
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            # No complete line yet; leave the offset untouched.
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        complete = data[: last_newline + 1]
        progressed = False
        compactions = 0
        for raw in complete.splitlines():
            line = raw.decode("utf-8", errors="replace")
            if _is_codex_compaction_line(line):
                compactions += 1
                continue
            if _is_effective_codex_session_line(line):
                progressed = True
        return _SessionReadResult(
            progressed=progressed,
            compactions=compactions,
            consumed_bytes=len(complete),
        )


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


class SupervisedEngineer:
    """Run the engineer with reviewer-gated retries.

    Stateless across calls. Construct once with backends, call ``run``
    per task.
    """

    def __init__(
        self,
        *,
        engineer_runner: RunnerBackend,
        reviewer: Reviewer,
        engineer_config: EngineerConfig,
        reviewer_config: ReviewerConfig,
    ) -> None:
        self.engineer_runner = engineer_runner
        self.reviewer = reviewer
        self.engineer_config = engineer_config
        self.reviewer_config = reviewer_config

    def run(
        self,
        *,
        objective: str,
        engineer_prompt_builder: Callable[[str | None], str],
        supervised_config: SupervisedConfig,
        workdir: Path,
        on_event: Callable[[dict], None] | None = None,
        seed_thread_id: str | None = None,
        failed_tool_ledger: _AdvisoryLedger | None = None,
        scope: str = "",
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action)`` is called once per round.
        On round 1, ``next_action`` is ``None``; on subsequent rounds,
        it is the reviewer's ``next_action`` from the previous round.
        The builder is responsible for assembling the full engineer
        prompt (task + skill block + injection text).

        Codex session continuity: round N+1 reuses round N's
        ``thread_id`` as ``resume_thread_id``. ``seed_thread_id`` (if
        provided) seeds round 1, allowing higher layers (e.g.
        life chat) to thread continuity *across* missions, not just
        across rounds.

        Returns ``(status, rounds, final_message, reason, last_thread_id)``.
        """
        rounds: list[RoundRecord] = []
        last_engineer_message = ""
        last_next_action: str | None = None
        no_progress_streak = 0
        semantic_stall_streak = 0
        backend_failure_streak = 0
        current_thread_id: str | None = seed_thread_id
        # Curated working-memory checkpoint. Loaded once (cross-mission / crash
        # continuity), carried in memory across rounds, re-authored by the
        # reviewer each round, and persisted after each verdict. It is what a
        # *fresh* engineer session reads after a session roll — so a rolled
        # session resumes from a small curated handoff, never the giant
        # compacted history that caused the amnesia loop.
        checkpoint = load_checkpoint(supervised_config.checkpoint_path)
        # Rounds the current Codex thread has lived for *this mission*. We
        # proactively roll (drop) the thread once it reaches the shift limit so
        # no single session accumulates enough history to repeatedly trigger
        # codex's lossy auto-compaction. NOTE: this counter resets each mission,
        # so it only bounds *within-mission* growth. The cross-mission bound
        # (a thread resumed across many short missions) is the token-size roll
        # in the loop below — see ``thread_token_limit``.
        rounds_on_thread = 0
        # Input-token count reported by the previous engineer round. Used by the
        # token-size session roll to detect (and drop) a thread that has grown
        # past the model's usable context. 0 on the first round of a mission, so
        # a normally-sized inherited thread is still resumed.
        last_input_tokens = 0

        for round_index in range(1, supervised_config.max_rounds + 1):
            engineer_prompt = engineer_prompt_builder(last_next_action)
            # Prepend the curated working-memory block (same splice mechanism
            # as the failed-tool advisory below). This is the engineer's only
            # memory of prior rounds once the session has been rolled.
            checkpoint_block = checkpoint.render_for_engineer()
            if checkpoint_block:
                engineer_prompt = checkpoint_block + "\n\n" + engineer_prompt
            # Repeated-tool-failure interrupt: if the same tool/command has
            # failed multiple times this mission and we haven't yet
            # nudged the agent about it, splice an advisory at the top
            # of this round's prompt. The ledger tracks "already nudged"
            # so the warning fires only once per tool per mission, not
            # every subsequent round.
            if failed_tool_ledger is not None:
                try:
                    advisory = failed_tool_ledger.render_advisory()
                except Exception:  # noqa: BLE001 — ledger must never break the loop
                    advisory = ""
                if advisory:
                    engineer_prompt = advisory + "\n\n" + engineer_prompt
                    if on_event:
                        on_event({
                            "type": "engineer.failure_nudge",
                            "round": round_index,
                            "text": "repeated tool failures detected — advisory injected",
                        })
            # Token-size session roll: the cross-mission counterpart to the
            # round-count roll below. A thread resumed across many short
            # missions carries the entire cross-mission transcript while
            # ``rounds_on_thread`` keeps resetting, so the round-count roll
            # never fires and the thread bloats past the model's usable
            # context — forcing codex's *lossy* auto-compaction and the
            # amnesia/re-read loop. Bounding by the previous round's reported
            # input-token count catches an inherited bloated thread on its first
            # round and drops it; subsequent rounds (and missions) then continue
            # on fresh, small threads. ``last_input_tokens`` is 0 on round 1 of
            # a fresh mission, so a normally-sized inherited thread is resumed.
            token_limit = int(getattr(supervised_config, "thread_token_limit", 0) or 0)
            if (
                token_limit > 0
                and current_thread_id is not None
                and last_input_tokens >= token_limit
            ):
                if on_event:
                    on_event({
                        "type": "session.roll",
                        "round": round_index,
                        "reason": "token_limit",
                        "input_tokens": last_input_tokens,
                        "text": (
                            f"rolling codex session: prior round used "
                            f"{last_input_tokens} input tokens (>= {token_limit}) "
                            "— fresh session resumes from checkpoint"
                        ),
                    })
                current_thread_id = None
                rounds_on_thread = 0
            # Proactive session roll: once the current Codex thread has lived
            # for the shift limit, drop it so THIS round starts a fresh session
            # seeded only by the curated checkpoint (prepended above), not the
            # accumulated history. This is the structural bound that prevents
            # the repeated-auto-compaction amnesia loop — no watchdog needed.
            shift_limit = int(getattr(supervised_config, "shift_round_limit", 0) or 0)
            if (
                shift_limit > 0
                and current_thread_id is not None
                and rounds_on_thread >= shift_limit
            ):
                if on_event:
                    on_event({
                        "type": "session.roll",
                        "round": round_index,
                        "reason": "shift_limit",
                        "rounds_on_thread": rounds_on_thread,
                        "text": (
                            f"rolling codex session after {rounds_on_thread} "
                            "rounds — fresh session resumes from checkpoint"
                        ),
                    })
                current_thread_id = None
                rounds_on_thread = 0
            if on_event:
                on_event({
                    "type": "round.start",
                    "round": round_index,
                    "round_max": supervised_config.max_rounds,
                    "text": f"engineer round {round_index}"
                            + (" (resuming codex session)" if current_thread_id else ""),
                })
            engineer_result = self._run_engineer(
                prompt=engineer_prompt,
                workdir=workdir,
                run_label=f"engineer-r{round_index}",
                resume_thread_id=current_thread_id,
                supervised_config=supervised_config,
                on_event=on_event,
            )
            # Capture thread_id so the next round (and the next mission,
            # via the return value) can resume the same codex session.
            new_tid = getattr(engineer_result, "thread_id", None)
            fatal_error = getattr(engineer_result, "fatal_error", None)
            round_thread_id = new_tid or current_thread_id
            engineer_message = engineer_result.last_agent_message or ""
            last_engineer_message = engineer_message or last_engineer_message
            # Feed the token-size session roll at the top of the next round.
            last_input_tokens = int(getattr(engineer_result, "input_tokens", 0) or 0)

            # Phase-2 instrumentation: emit ``round.main.completed`` so the
            # supervisor's _CostTrackingSink can fold engineer-side token
            # counts into the iteration budget. Without this event the
            # cost sink only ever sees the reviewer half (and silently
            # under-charges) — leading to ``cost_usd=$0`` in the journal
            # when reviewer tokens were also missing pre-fix.
            if on_event:
                on_event({
                    "type": "round.main.completed",
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": round_thread_id,
                    "exit_code": getattr(engineer_result, "exit_code", 0),
                    "fatal_error": getattr(engineer_result, "fatal_error", None),
                    "last_message": engineer_message,
                    "input_tokens": int(getattr(engineer_result, "input_tokens", 0) or 0),
                    "cached_input_tokens": int(
                        getattr(engineer_result, "cached_input_tokens", 0) or 0
                    ),
                    "output_tokens": int(getattr(engineer_result, "output_tokens", 0) or 0),
                    "usage_scope": "delta",
                })

            if should_clear_thread_id_after_outcome(status="", fatal_error=fatal_error):
                # Context-pressure / poisoned-session / backend-failure roll.
                # The checkpoint carries memory across this drop, so a cleared
                # thread is a clean rebirth, not amnesia.
                current_thread_id = None
                rounds_on_thread = 0
            elif new_tid:
                if new_tid == current_thread_id:
                    rounds_on_thread += 1
                else:
                    # Brand-new thread id (fresh session this round).
                    rounds_on_thread = 1
                current_thread_id = new_tid

            if fatal_error_looks_like_daemon_stop_request(fatal_error):
                review = daemon_stop_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text="review: skipped (daemon stop requested)",
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    checks=[],
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                ))
                return (
                    "error",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    None,
                )

            if fatal_error_looks_like_backend_failure(fatal_error):
                backend_failure_streak += 1
                no_progress_streak = 0
                review = backend_failure_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                    streak=backend_failure_streak,
                    threshold=supervised_config.backend_failure_threshold,
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text=(
                            "review: skipped (backend failure) — "
                            f"{review.reason}"
                        ),
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    checks=[],
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                ))
                threshold = max(1, int(supervised_config.backend_failure_threshold or 1))
                if backend_failure_streak >= threshold or round_index >= supervised_config.max_rounds:
                    return (
                        "error",
                        rounds,
                        last_engineer_message,
                        review.reason,
                        None,
                    )
                backoff_seconds = max(
                    0.0, float(supervised_config.backend_failure_backoff_seconds or 0.0)
                )
                if backoff_seconds:
                    if on_event:
                        on_event({
                            "type": "round.backend_failure.backoff",
                            "round_index": round_index,
                            "round_max": supervised_config.max_rounds,
                            "seconds": backoff_seconds,
                            "text": (
                                "backend failure; retrying in a fresh Codex session "
                                f"after {backoff_seconds:.1f}s"
                            ),
                        })
                    time.sleep(backoff_seconds)
                last_next_action = review.next_action
                continue

            backend_failure_streak = 0
            if not _runner_result_has_successful_work_signal(
                engineer_result, engineer_message=engineer_message
            ):
                no_progress_streak += 1
            else:
                no_progress_streak = 0

            checks_results: list[CheckResult] = []
            if supervised_config.check_commands:
                checks_results = run_checks(
                    supervised_config.check_commands,
                    timeout_seconds=supervised_config.check_timeout_seconds,
                    cwd=str(workdir),
                )
                if on_event:
                    on_event({
                        "type": "checks.done",
                        "round": round_index,
                        "text": f"checks: {sum(1 for c in checks_results if c.passed)}/{len(checks_results)} pass",
                    })
            prev_round = rounds[-1] if rounds else None
            prev_review = getattr(prev_round, "review", None) if prev_round else None
            prev_review_summary = ""
            if prev_review is not None:
                prev_review_summary = (
                    getattr(prev_review, "round_summary_markdown", "")
                    or getattr(prev_review, "reason", "")
                    or ""
                )

            if on_event:
                on_event({
                    "type": "round.review.started",
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": supervised_config.session_id,
                })
            try:
                review = self.reviewer.evaluate(
                    objective=objective,
                    round_index=round_index,
                    session_id=supervised_config.session_id,
                    main_summary=engineer_message or "(no message)",
                    main_error=engineer_result.fatal_error,
                    checks=checks_results,
                    config=self.reviewer_config,
                    engineer_reasoning_summary=engineer_message or "",
                    prev_review_summary=prev_review_summary,
                    scope=scope,
                    prior_checkpoint=checkpoint.to_dict(),
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"reviewer raised {type(exc).__name__}: {exc}"
                log.exception("reviewer raised during supervised round")
                review = ReviewDecision(
                    status="blocked",
                    confidence=0.0,
                    reason=msg,
                    next_action="Resolve the reviewer runner failure before retrying.",
                    round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                    completion_summary_markdown="",
                    failure_cause="environmental",
                )
            review = _coerce_review_for_failed_checks(review, checks_results)
            # SEMANTIC stall tracking: the engineer can stay busy (non-empty
            # messages, so ``no_progress_streak`` keeps resetting) yet make no
            # real advance round after round. The reviewer reports this via
            # ``planner_report.forward_progress``. Count only EXPLICIT boolean
            # ``False`` on a ``continue`` round — a missing/omitted field is
            # treated as "unknown", never as a stall — so a reviewer or schema
            # hiccup cannot falsely kill a healthy long-running mission.
            # ``_classify`` bails as ``no_progress`` once it crosses
            # ``stall_threshold``.
            planner_report = getattr(review, "planner_report", None)
            raw_forward_progress = (
                planner_report.get("forward_progress")
                if isinstance(planner_report, dict)
                else None
            )
            if review.status == "continue" and raw_forward_progress is False:
                semantic_stall_streak += 1
                if on_event and semantic_stall_streak > 0:
                    on_event({
                        "type": "round.stall",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "semantic_stall_streak": semantic_stall_streak,
                        "stall_threshold": supervised_config.stall_threshold,
                        "text": (
                            f"no forward progress {semantic_stall_streak}/"
                            f"{supervised_config.stall_threshold} rounds"
                        ),
                    })
            else:
                semantic_stall_streak = 0
            # Update curated working memory from the reviewer-authored
            # checkpoint. Fail-soft: an empty/malformed checkpoint keeps the
            # prior one rather than wiping memory on a noisy verdict.
            new_checkpoint = CheckpointState.from_dict(getattr(review, "checkpoint", {}))
            if not new_checkpoint.is_empty():
                checkpoint = new_checkpoint.stamped(round_no=round_index)
                save_checkpoint(supervised_config.checkpoint_path, checkpoint)
            if on_event:
                on_event(_review_event_payload(
                    review,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    text=f"review: {review.status} (conf={review.confidence:.2f}) — {review.reason}",
                ))
            rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                checks=checks_results,
                review=review,
                fatal_error=engineer_result.fatal_error,
            ))

            terminal_status, reason = self._classify(
                review=review,
                checks_results=checks_results,
                no_progress_streak=no_progress_streak,
                no_progress_threshold=supervised_config.no_progress_threshold,
                semantic_stall_streak=semantic_stall_streak,
                stall_threshold=supervised_config.stall_threshold,
                round_index=round_index,
                max_rounds=supervised_config.max_rounds,
            )
            if terminal_status is not None:
                return (
                    terminal_status,
                    rounds,
                    last_engineer_message,
                    reason,
                    None
                    if should_clear_thread_id_after_outcome(
                        status=terminal_status,
                        fatal_error=fatal_error,
                    )
                    else current_thread_id,
                )

            last_next_action = review.next_action

        return (
            "max_rounds",
            rounds,
            last_engineer_message,
            f"Hit max_rounds={supervised_config.max_rounds} without reviewer-confirmed completion.",
            current_thread_id,
        )

    def _run_engineer(
        self,
        *,
        prompt: str,
        workdir: Path,
        run_label: str,
        resume_thread_id: str | None = None,
        supervised_config: SupervisedConfig | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> RunnerResult:
        effective_progress_provider: Callable[[], str | None] | None = None
        effective_progress_watchdog: _EffectiveProgressWatchdog | None = None
        hard_idle_seconds = 0
        if supervised_config is not None:
            timeout_seconds = int(
                supervised_config.effective_progress_timeout_seconds or 0
            )
            hard_idle_seconds = int(supervised_config.runner_hard_idle_seconds or 0)
            if timeout_seconds > 0:
                effective_progress_watchdog = _EffectiveProgressWatchdog(
                    workdir=workdir,
                    timeout_seconds=timeout_seconds,
                    check_interval_seconds=(
                        supervised_config.effective_progress_check_interval_seconds
                    ),
                    on_event=on_event,
                    run_label=run_label,
                    compaction_limit=supervised_config.round_compaction_limit,
                )
                effective_progress_provider = effective_progress_watchdog.interrupt_reason
        try:
            result = self.engineer_runner.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=self.engineer_config.model,
                    reasoning_effort=self.engineer_config.reasoning_effort,
                    extra_args=self.engineer_config.extra_args,
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    working_dir=str(workdir),
                    external_interrupt_reason_provider=effective_progress_provider,
                    watchdog_hard_idle_seconds=hard_idle_seconds,
                ),
                run_label=run_label,
                resume_thread_id=resume_thread_id,
            )
            if (
                effective_progress_watchdog is not None
                and effective_progress_watchdog.current_interrupt_reason()
                and not getattr(result, "fatal_error", None)
            ):
                return replace(
                    result,
                    exit_code=(
                        int(getattr(result, "exit_code", 0) or 0)
                        if int(getattr(result, "exit_code", 0) or 0) != 0
                        else -1
                    ),
                    fatal_error=effective_progress_watchdog.current_interrupt_reason(),
                )
            return result
        except Exception as exc:  # noqa: BLE001
            msg = f"engineer runner raised {type(exc).__name__}: {exc}"
            log.exception("engineer runner raised during %s", run_label)
            return RunnerResult(
                exit_code=-1,
                fatal_error=msg,
                stderr_lines=[msg],
            )

    @staticmethod
    def _classify(
        *,
        review: ReviewDecision,
        checks_results: list[CheckResult],
        no_progress_streak: int,
        no_progress_threshold: int,
        semantic_stall_streak: int = 0,
        stall_threshold: int = 0,
        round_index: int,
        max_rounds: int,
    ) -> tuple[LoopStatus | None, str]:
        if review.status == "done" and (not checks_results or all_checks_passed(checks_results)):
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status == "blocked":
            return "blocked", review.reason or "Reviewer blocked progress."
        if no_progress_streak >= no_progress_threshold:
            return (
                "no_progress",
                "Engineer produced no effective output for "
                f"{no_progress_streak} consecutive rounds.",
            )
        if (
            stall_threshold > 0
            and semantic_stall_streak >= stall_threshold
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                "Reviewer reported no forward progress for "
                f"{semantic_stall_streak} consecutive rounds.",
            )
        # done but checks failed — treat as continue (reviewer was wrong /
        # checks discovered residual gap).
        if review.status == "done" and checks_results and not all_checks_passed(checks_results):
            log.info(
                "round %d: reviewer said done but %d/%d checks failed; "
                "continuing",
                round_index,
                sum(1 for c in checks_results if not c.passed),
                len(checks_results),
            )
        return None, ""


def backend_failure_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
    streak: int,
    threshold: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    threshold = max(1, int(threshold or 1))
    retry_text = (
        "Retry in a fresh Codex session; do not resume the failed thread. "
        "If this repeats, pause the daemon and reduce concurrent Codex load."
    )
    return ReviewDecision(
        status="continue",
        confidence=0.0,
        reason=(
            "Engineer backend failed before a trustworthy completed turn; "
            f"reviewer skipped. backend_failure_streak={streak}/{threshold}; "
            f"error={error_text}"
        ),
        next_action=retry_text,
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because the engineer backend reported a transient "
            "infrastructure failure.\n"
            f"- Error: {error_text}\n"
            f"- Consecutive backend failures: {streak}/{threshold}\n"
        ),
        completion_summary_markdown="",
        failure_cause="environmental",
    )


def daemon_stop_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        confidence=0.0,
        reason=(
            "Engineer interrupted because daemon shutdown was requested; "
            f"no backend retry was attempted. error={error_text}"
        ),
        next_action=(
            "Restart the daemon when ready; the continuous planner will choose "
            "the next concrete task from the persisted project state."
        ),
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because daemon shutdown was requested.\n"
            f"- Error: {error_text}\n"
        ),
        completion_summary_markdown="",
        failure_cause="operator_interrupt",
    )


_GATE_MARKER = "🛡  Automated gates"
_GATE_FAIL_LINE_PREFIX = "  ❌ "


def _extract_gate_failures(check: CheckResult) -> list[str]:
    """Pull structured gate failure summaries out of a stage_check
    ``CheckResult.output_tail``. Returns one short line per failed gate
    (e.g. ``"gate:evidence_chain — 1 chain issue(s) across 9 claim(s)"``)
    so the reviewer's next_action can name them specifically instead of
    saying "the acceptance checks still fail".
    """
    tail = (check.output_tail or "")
    if _GATE_MARKER not in tail:
        return []
    lines = tail.splitlines()
    failures: list[str] = []
    in_section = False
    for line in lines:
        if _GATE_MARKER in line:
            in_section = True
            continue
        if not in_section:
            continue
        # Section ends at the next blank line or the next top-level header.
        stripped = line.rstrip()
        if not stripped:
            break
        if stripped.startswith("📋") or stripped.startswith("❌") or stripped.startswith("✅"):
            break
        if line.startswith(_GATE_FAIL_LINE_PREFIX):
            # "  ❌ evidence_chain — 1 chain issue(s) ..." → strip the prefix.
            failures.append("gate:" + line[len(_GATE_FAIL_LINE_PREFIX):].strip())
    return failures


def _fallback_failed_check_handoff(checks: list[CheckResult]) -> str:
    failed = [check for check in checks if not check.passed]
    if not failed:
        return ""

    # Surface automated-gate failures specifically so the reviewer's
    # next_action names which gate vetoed the round (and why), instead of
    # the generic "rerun the failed command" handoff.
    gate_failures: list[str] = []
    for check in failed:
        gate_failures.extend(_extract_gate_failures(check))

    if gate_failures:
        lines = [
            "Automated research-factory gates vetoed this round. "
            "Address each gate failure listed below before claiming done; "
            "the gate validators are Python, not LLM heuristics, so the "
            "fix must change real artifacts (claims_to_evidence.tsv, "
            "evidence bundles, baseline reproductions, benchmark coverage).",
        ]
        for index, failure in enumerate(gate_failures, start=1):
            lines.append(f"{index}. {failure}")
        lines.append(
            "After fixing, rerun "
            "`python -m argus_skill.tools.stage_check --project-root .` "
            "and verify the gate section shows ✅ for every gate before "
            "marking the round done."
        )
        return "\n".join(lines)

    fallback_lines: list[str] = [
        "The acceptance checks still fail. Convert the validator blockers into concrete fixes, "
        "then rerun the exact failed command before claiming completion.",
    ]
    for index, check in enumerate(failed, start=1):
        fallback_lines.append(f"{index}. `{check.command}` exited {check.exit_code}.")
    return "\n".join(fallback_lines)


def _coerce_review_for_failed_checks(
    review: ReviewDecision,
    checks: list[CheckResult],
) -> ReviewDecision:
    failed = [check for check in checks if not check.passed]
    if not failed:
        return review

    next_action = (review.next_action or "").strip()
    if review.status != "done":
        return replace(review, next_action=next_action or _fallback_failed_check_handoff(failed))

    if not next_action or next_action.casefold().startswith("no further action"):
        next_action = _fallback_failed_check_handoff(failed)
    failed_commands = ", ".join(f"`{check.command}` exited {check.exit_code}" for check in failed)
    return replace(
        review,
        status="continue",
        reason=(
            "Acceptance checks failed after the engineer turn, so the task cannot be done: "
            f"{failed_commands}."
        ),
        next_action=next_action,
    )


__all__ = [
    "EngineerConfig",
    "SupervisedConfig",
    "SupervisedEngineer",
    "LoopOutcome",
    "backend_failure_review_decision",
    "daemon_stop_review_decision",
    "fatal_error_looks_like_backend_failure",
    "fatal_error_looks_like_daemon_stop_request",
    "fatal_error_looks_like_effective_progress_timeout",
    "fatal_error_looks_like_compaction_thrash",
    "fatal_error_looks_like_recoverable_reconnect",
    "should_clear_thread_id_after_outcome",
]
