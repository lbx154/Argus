"""Supervisor idle, stop, inbox, and backoff state machine."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ..terminal_state import build_terminal_idle_signature
from ._constants import (
    IDLE_BACKOFF_BASE_SECONDS,
    IDLE_BACKOFF_CAP_SECONDS,
    PLAN_TERMINAL_IDLE,
    PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS,
)

log = logging.getLogger(__name__)
_DAEMON_IDLE_EXIT_DEFAULT_MINUTES = 0.0
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
    def _artifact_root(self) -> Path:
        raise NotImplementedError

    def _runtime_failure_circuit_block(
        self,
        *,
        item: Any | None = None,
    ) -> dict[str, Any] | None:
        """Hold dispatch while the same loaded runtime owns an open circuit."""
        item_tags = {
            str(tag).strip().lower()
            for tag in (getattr(item, "tags", None) or [])
            if str(tag).strip()
        }
        if item_tags & {"framework_maintenance", "runtime_failure_canary"}:
            return None
        try:
            from ..runtime_failure_circuit import active_runtime_failure_circuit

            circuit = active_runtime_failure_circuit(self.memory.root)
        except Exception:  # noqa: BLE001 - a corrupt advisory file must not crash host
            log.exception("failed to inspect runtime failure circuit")
            return None
        if circuit is None:
            return None
        fingerprint = str(circuit.get("fingerprint") or "")
        reason = (
            "runtime failure circuit is open; install or canary a changed Argus "
            f"runtime before dispatching more work ({fingerprint})"
        )
        if self._should_journal_idle_repeat("runtime_failure_circuit"):
            self._emit({
                "type": EventType.LIFE_RUNTIME_FAILURE_CIRCUIT_BLOCKED,
                "item_id": str(getattr(item, "id", "") or ""),
                "fingerprint": fingerprint,
                "exception_type": circuit.get("exception_type"),
                "callsite": circuit.get("callsite"),
                "normalized_error": circuit.get("normalized_error"),
                "occurrence_count": circuit.get("occurrence_count"),
                "runtime_identity": circuit.get("runtime_identity"),
                "operator_alert": True,
                "reason": reason,
            })
            self._emit_status(reason)
        return {
            "status": "infra_blocked",
            "item_id": str(getattr(item, "id", "") or ""),
            "reason": reason,
            "fingerprint": fingerprint,
            "recoverable": True,
        }

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
            try:
                from ...manager.directive import record_operator_messages

                record_operator_messages(
                    self.memory.root,
                    out,
                    manager=self._bound_manager(),
                )
            except Exception:  # noqa: BLE001 - inbox delivery remains fail-soft
                log.exception("could not persist operator steering ledger")
            self._emit({
                "type": EventType.LIFE_INBOX_DRAINED,
                "count": len(out),
                "messages": out,
            })
        return out

    def _resolve_pending_question_from_inbox(self, pending_questions: list[Any]) -> bool:
        """Route unconsumed operator input through Manager before deferring Planner."""
        resolver = getattr(self.config, "pending_question_resolver", None)
        if len(pending_questions) != 1 or not callable(resolver):
            return False
        messages = self._drain_user_inbox(max_messages=1)
        if not messages:
            return False
        item = pending_questions[0]
        for message in messages:
            try:
                result = resolver(item, message)
            except Exception:  # noqa: BLE001
                log.exception(
                    "pending-question resolver failed for backlog item %s",
                    getattr(item, "id", ""),
                )
                continue
            if isinstance(result, dict) and bool(result.get("resolved")):
                self._reset_idle_backoff()
                # The resolver consumed this message, but the next Planner cycle
                # still needs it as durable guidance. Keep an in-process carryover;
                # the item and transcript remain the restart-safe record.
                carryover = getattr(self, "_operator_guidance_carryover", None)
                if carryover is None:
                    carryover = []
                    self._operator_guidance_carryover = carryover
                carryover.append(message)
                self._emit_status(
                    "operator inbox resolved pending question "
                    f"for backlog item {getattr(item, 'id', '')}"
                )
                return True
        return False

    def _take_operator_guidance_carryover(self) -> list[str]:
        """Operator guidance consumed elsewhere, handed to the Planner once."""
        carryover = getattr(self, "_operator_guidance_carryover", None) or []
        self._operator_guidance_carryover = []
        return list(carryover)

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        # In continuous mode, max_missions is not a hard cap — the
        # planner generates new work indefinitely until it declares
        # the project done. Only the host-global daily budget is enforced.
        if not self.config.continuous and self.config.budget.max_missions > 0:
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
        allowed, _reason = self.config.budget.can_start(
            global_root=self._budget_global_root(),
        )
        if not allowed:
            try:
                if self.memory.backlog.next_pending() is not None:
                    return "paused_budget"
            except Exception:  # noqa: BLE001
                pass
            return "global daily budget exhausted"
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
        delay = IDLE_BACKOFF_BASE_SECONDS
        remaining_doublings = n - 1
        while remaining_doublings > 0 and delay < IDLE_BACKOFF_CAP_SECONDS:
            delay = min(IDLE_BACKOFF_CAP_SECONDS, delay * 2)
            remaining_doublings -= 1
        return delay

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

    def _enter_pause_backoff(self) -> float:
        """Back off a recoverable pause without starting the idle-exit clock."""
        self._consecutive_idle_planner_cycles += 1
        self._idle_since = None
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
        idle_since = getattr(self, "_idle_since", None)
        if idle_since is None:
            return ""
        messages = self._drain_user_inbox()
        if messages:
            carryover = getattr(self, "_operator_guidance_carryover", None)
            if carryover is None:
                carryover = []
                self._operator_guidance_carryover = carryover
            carryover.extend(messages)
            self._reset_idle_backoff()
            self._emit_status("operator guidance woke the idle Planner")
            return ""
        cap = _idle_exit_seconds()
        if cap <= 0:
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
        """Fingerprint only state that can justify another Planner decision."""
        try:
            backlog = sorted(
                (
                    str(getattr(item, "id", "")),
                    str(getattr(item, "title", "")),
                    str(getattr(item, "status", "")),
                )
                for item in self.memory.backlog.active()
            )
        except Exception:  # noqa: BLE001
            backlog = []
        completion_contract = None
        try:
            for entry in reversed(self.memory.journal.all()):
                if str(getattr(entry, "kind", "") or "") != "mission_complete":
                    continue
                extra = getattr(entry, "extra", None)
                if not isinstance(extra, dict):
                    continue
                completion_contract = {
                    key: extra.get(key)
                    for key in (
                        "success",
                        "status",
                        "scope",
                        "final_submission_certified",
                        "research_result",
                        "stop_kind",
                    )
                    if key in extra
                }
                break
        except Exception:  # noqa: BLE001
            pass
        return build_terminal_idle_signature(
            objective=str(self.config.continuous_objective or ""),
            stage=str(self._current_pipeline_stage() or ""),
            backlog=backlog,
            artifact_root=self._artifact_root(),
            project_root=self._planner_workdir(),
            state_root=Path(self.memory.root),
            completion_contract=completion_contract,
        )

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
