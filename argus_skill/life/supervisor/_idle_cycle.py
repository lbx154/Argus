"""Supervisor idle, stop, inbox, and backoff state machine."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from ...core.event_catalog import EventType
from ._constants import (
    IDLE_BACKOFF_BASE_SECONDS,
    IDLE_BACKOFF_CAP_SECONDS,
    PLAN_TERMINAL_IDLE,
    PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS,
)

log = logging.getLogger(__name__)
_DAEMON_IDLE_EXIT_DEFAULT_MINUTES = 30.0

def _idle_exit_seconds() -> float:
    """Idle wall-clock (s) before a continuous daemon auto-exits; 0 = never."""
    raw = os.environ.get("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "").strip()
    if not raw:
        return _DAEMON_IDLE_EXIT_DEFAULT_MINUTES * 60.0
    try:
        minutes = float(raw)
    except ValueError:
        return _DAEMON_IDLE_EXIT_DEFAULT_MINUTES * 60.0
    return max(0.0, minutes) * 60.0


class IdleCycleMixin:
    def _drain_user_inbox(self, *, max_messages: int = 10) -> list[str]:
        """Pull all pending operator nudges from the configured inbox.

        Returns up to ``max_messages`` lines (oldest-first). Empty list
        if no inbox is configured or nothing is pending. Any exception
        from the user-supplied callable is swallowed — a flaky bus
        must never break a mission.
        """
        cb = getattr(self.config, "user_inbox", None)
        if cb is None:
            return []
        out: list[str] = []
        for _ in range(max(1, int(max_messages))):
            try:
                msg = cb()
            except Exception:  # noqa: BLE001
                log.exception("user_inbox callable raised; ignoring")
                break
            if not msg:
                break
            text = str(msg).strip()
            if text:
                out.append(text)
        if out:
            self._emit({
                "type": EventType.LIFE_INBOX_DRAINED,
                "count": len(out),
                "messages": out,
            })
        return out

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        # In continuous mode, max_missions is not a hard cap — the
        # planner generates new work indefinitely until it declares
        # the project done. Only daily budget is enforced.
        if not self.config.continuous:
            if self._missions_started >= self.config.budget.max_missions:
                # Suppress the cap message when there's no held-back work.
                # Treats "you asked for one mission, you got one" as silent
                # success rather than a noisy guardrail trip.
                try:
                    more_pending = self.memory.backlog.next_pending() is not None
                except Exception:  # noqa: BLE001
                    more_pending = False
                if more_pending:
                    return f"max-missions cap reached ({self.config.budget.max_missions})"
                return "__silent_stop__"
        if self.config.budget.remaining_today(self.memory.journal) <= 0:
            return "daily budget exhausted"
        return ""

    def _wait_idle(self) -> bool:
        """Sleep ``poll_interval_seconds`` honouring stop_event.

        Returns True if stop_event fired during the wait."""
        ev = self.config.stop_event
        if ev is None:
            time.sleep(self.config.poll_interval_seconds)
            return False
        return ev.wait(self.config.poll_interval_seconds)

    def _idle_backoff_seconds(self) -> float:
        """Exponential re-check sleep for consecutive no-work plan-cycles.

        ``_consecutive_idle_planner_cycles`` is incremented by the caller
        BEFORE calling this; cycle 1 → base, doubling each cycle, capped.
        """
        n = max(1, int(self._consecutive_idle_planner_cycles))
        return min(IDLE_BACKOFF_CAP_SECONDS, IDLE_BACKOFF_BASE_SECONDS * (2 ** (n - 1)))

    def _reset_idle_backoff(self) -> None:
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._idle_since = None
        self._last_open_ended_project_done_signature = ""

    def _enter_idle_backoff(self) -> float:
        """Register one more no-work plan-cycle and return the suggested sleep."""
        self._consecutive_idle_planner_cycles += 1
        if getattr(self, "_idle_since", None) is None:
            self._idle_since = time.monotonic()
        self._suggested_sleep_s = self._idle_backoff_seconds()
        return self._suggested_sleep_s

    def _maybe_idle_timeout(self) -> str:
        """``"idle_timeout"`` once a continuous daemon has been idle too long.

        Idle wall-clock is measured from ``_idle_since`` (first no-work pass)
        and spans the daemon's outer-loop sleeps. Returns ``""`` when not in
        continuous mode, when the feature is disabled (cap ≤ 0), or when the
        streak is still within the window — so the only behaviour change is: a
        genuinely idle 7×24 daemon releases its slot after the cap.
        """
        if not getattr(self.config, "continuous", False):
            return ""
        cap = _idle_exit_seconds()
        idle_since = getattr(self, "_idle_since", None)
        if cap <= 0 or idle_since is None:
            return ""
        if time.monotonic() - idle_since >= cap:
            return "idle_timeout"
        return ""

    def _should_journal_idle_repeat(self, kind: str) -> bool:
        """Heartbeat-gate repetitive idle/waiting JOURNAL appends.

        Keyed on ``kind`` ALONE — deliberately ignoring the reason text, because
        the planner rewrites the reason every cycle (fresh audit timestamps and
        details), so a reason-keyed gate would never collapse the spam. Returns
        True (and updates the suppression state) when the kind differs from the
        last idle entry or a heartbeat window has elapsed; False for an
        in-window repeat that should be suppressed — so a long external wait
        cannot flood, and poison, the planner's own next-cycle context. The
        per-cycle event + status still carry the live reason, so operator
        visibility is unchanged. State read via ``getattr`` defaults for
        test-stub safety.
        """
        now = time.monotonic()
        last_sig = getattr(self, "_last_planner_idle_sig", None)
        last_at = getattr(self, "_last_planner_idle_at", 0.0)
        if kind != last_sig or (
            now - last_at
        ) >= PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS:
            self._last_planner_idle_sig = kind
            self._last_planner_idle_at = now
            return True
        return False

    def _open_ended_terminal_idle_signature(self) -> str:
        """Cheap observable-state fingerprint for open-ended terminal idling.

        The signature deliberately excludes the life journal/backlog files
        written by the supervisor itself, so a skipped planner cycle does not
        invalidate its own idle state. It includes runtime context, objective,
        pipeline stage, backlog statuses, and project file metadata so operator
        edits or daemon/runtime source changes cause the planner to run again.
        """
        digest = hashlib.sha256()
        digest.update(b"open-ended-terminal-idle-v1\0")
        digest.update(str(self.config.continuous_objective or "").encode())
        digest.update(b"\0")
        digest.update(str(self._current_pipeline_stage() or "").encode())
        digest.update(b"\0")
        digest.update(str(self._planner_project_context() or "").encode())
        digest.update(b"\0")
        try:
            for item in self.memory.backlog.all():
                digest.update(str(getattr(item, "id", "")).encode())
                digest.update(b"\t")
                digest.update(str(getattr(item, "title", "")).encode())
                digest.update(b"\t")
                digest.update(str(getattr(item, "status", "")).encode())
                digest.update(b"\n")
        except Exception as exc:  # noqa: BLE001
            digest.update(f"backlog-error:{type(exc).__name__}:{exc}".encode())
            digest.update(b"\0")

        root = self._planner_workdir()
        ignored_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "node_modules",
        }
        ignored_files = {
            "events.jsonl",
            "journal.jsonl",
            "backlog.jsonl",
            "continuous.json",
            "daemon.log",
            "daemon.status.json",
            "daemon.pid",
        }
        try:
            root = root.resolve()
            count = 0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if d not in ignored_dirs and not d.endswith(".egg-info")
                ]
                rel_dir = Path(dirpath).relative_to(root)
                if any(part in ignored_dirs for part in rel_dir.parts):
                    continue
                for name in sorted(filenames):
                    if name in ignored_files:
                        continue
                    path = Path(dirpath) / name
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    try:
                        rel = path.relative_to(root)
                    except ValueError:
                        rel = path
                    digest.update(str(rel).encode("utf-8", "surrogateescape"))
                    digest.update(b"\t")
                    digest.update(str(st.st_size).encode())
                    digest.update(b"\t")
                    digest.update(str(st.st_mtime_ns).encode())
                    digest.update(b"\n")
                    count += 1
                    if count >= 5000:
                        digest.update(b"file-scan-truncated\0")
                        raise StopIteration
        except StopIteration:
            pass
        except Exception as exc:  # noqa: BLE001
            digest.update(f"fs-error:{type(exc).__name__}:{exc}".encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def _maybe_idle_after_unchanged_open_ended_done(self) -> str | None:
        if not (
            getattr(self.config, "continuous", False)
            and getattr(self.config, "continuous_objective", "")
            and getattr(self.config, "open_ended", False)
            and getattr(self, "_last_open_ended_project_done_signature", "")
        ):
            return None

        # New operator input is state change. Drain it into the inbox context so
        # the next planner call can see it, then re-plan normally.
        if self._drain_user_inbox():
            self._last_open_ended_project_done_signature = ""
            return None

        current = self._open_ended_terminal_idle_signature()
        if current != self._last_open_ended_project_done_signature:
            self._last_open_ended_project_done_signature = ""
            return None

        sleep_s = self._enter_idle_backoff()
        self._emit({
            "type": EventType.LIFE_PLANNER_TERMINAL_IDLE,
            "cycle": self._planning_cycles,
            "reason": "open-ended project_done unchanged since last planner verdict",
            "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
            "suggested_sleep_s": sleep_s,
        })
        self._emit_status(
            "planner: project already done and unchanged; idling without planner call"
        )
        return PLAN_TERMINAL_IDLE


__all__ = ["IdleCycleMixin", "_idle_exit_seconds"]
