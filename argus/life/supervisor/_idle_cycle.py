"""Supervisor idle, stop, inbox, and backoff state machine."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ...core.runner_errors import is_execution_host_startup_error
from ...core.stop_kinds import RUNTIME_RESUMED_PARKS
from ..memory import park_reason
from ..terminal_state import build_terminal_idle_signature
from ._constants import (
    IDLE_BACKOFF_BASE_SECONDS,
    IDLE_BACKOFF_CAP_SECONDS,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
    PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS,
    PROXY_DECISION_ATTEMPTS,
)

if TYPE_CHECKING:
    from ._config import LifeSupervisorConfig

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
    if TYPE_CHECKING:
        config: LifeSupervisorConfig

        def _bound_manager(self) -> Any: ...
        def _operator_chinese(self) -> bool: ...
        def _emit_status(self, text: str) -> None: ...
        def _tell_status(self, kind: str, **params: Any) -> None: ...
        def _operator_words(self, kind: str, **params: Any) -> dict[str, str]: ...
        def _tell_operator(self, notice_id: str, kind: str, **params: Any) -> None: ...

    def _artifact_root(self) -> Path:
        raise NotImplementedError

    def _runtime_failure_circuit_block(
        self,
        *,
        item: Any | None = None,
    ) -> dict[str, Any] | None:
        """Hold dispatch and Planner turns while a circuit is open."""
        host_block = self._execution_host_failure_block(item=item)
        if host_block is not None:
            return host_block
        item_tags = {
            str(tag).strip().lower()
            for tag in (getattr(item, "tags", None) or [])
            if str(tag).strip()
        }
        try:
            from ..runtime_failure_circuit import PROVIDER_OUTAGE, active_runtime_failure_circuit

            circuit_root = self.memory.root
            circuit = active_runtime_failure_circuit(circuit_root)
        except Exception:  # noqa: BLE001 - a corrupt advisory file must not crash host
            log.exception("failed to inspect runtime failure circuit")
            return None
        if circuit is None:
            return None
        fingerprint = str(circuit.get("fingerprint") or "")
        outage = fingerprint == PROVIDER_OUTAGE
        # Maintenance repairs Argus's own code; it cannot bring a provider back.
        if "framework_maintenance" in item_tags and not outage:
            return None
        # Only the canary this circuit admitted passes. A tag it never handed
        # out (an earlier circuit's, or one copied onto another row) waits for
        # a half-open window like any other work.
        admitted = str(circuit.get("canary_item_id") or "")
        if (
            "runtime_failure_canary" in item_tags
            and admitted
            and admitted == str(getattr(item, "id", "") or "")
        ):
            return None
        if self._admit_runtime_failure_half_open(circuit_root, circuit, item=item):
            return None
        # The circuit's own fields carry the technical record.
        words = self._operator_words(
            "work_paused_service" if outage else "work_paused_internal"
        )
        reason = words["text"]
        if self._should_journal_idle_repeat("runtime_failure_circuit"):
            self._emit({
                "type": EventType.LIFE_RUNTIME_FAILURE_CIRCUIT_BLOCKED,
                **words,
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
            "status": "runtime_block",
            "item_id": str(getattr(item, "id", "") or ""),
            "reason": reason,
            "fingerprint": fingerprint,
            "recoverable": True,
        }

    def _admit_runtime_failure_half_open(
        self, circuit_root: Path, circuit: dict[str, Any], *, item: Any | None,
    ) -> bool:
        """Let one canary through an open circuit once a window opens.

        With an item, that item becomes the window's canary mission (tagged so
        its reviewed pass clears the circuit). Without one, the Planner gets
        the window's single turn, so an empty backlog can still produce the
        work a canary needs. Only one canary is in flight at a time.
        """
        from ..runtime_failure_circuit import (
            CANARY_TAG,
            claim_runtime_failure_half_open,
        )

        previous = str(circuit.get("canary_item_id") or "")
        try:
            if previous:
                row = next(
                    (
                        row
                        for row in reversed(self.memory.backlog.history())
                        if row.id == previous
                    ),
                    None,
                )
                # The claim is recorded before the item is tagged. An untagged
                # row means tagging never happened, so it is not in flight. A
                # canary that will still run (or resume by itself) is in
                # flight, so a new half-open window waits for it.
                if (
                    row is not None
                    and (
                        row.status in {"pending", "running"}
                        or park_reason(row) in RUNTIME_RESUMED_PARKS
                    )
                    and CANARY_TAG in (row.tags or [])
                ):
                    return False
            item_id = str(getattr(item, "id", "") or "")
            if not claim_runtime_failure_half_open(
                circuit_root,
                slot="mission" if item is not None else "planner",
                item_id=item_id,
                expected_canary_item_id=previous,
                facts=self._runtime_failure_canary_facts(),
            ):
                return False
            if item is not None:
                tags = list(dict.fromkeys([*(item.tags or []), CANARY_TAG]))
                self.memory.backlog.update(item_id, tags=tags)
                item.tags = tags
        except Exception:  # noqa: BLE001 - a failed admission leaves the circuit open
            log.exception("runtime failure circuit half-open admission failed")
            return False
        # The circuit file records the admission (window, canary id); the
        # status line is the operator-visible trace.
        self._emit_status(
            f"runtime failure circuit half-open: mission {item_id} runs as the "
            f"canary ({circuit.get('fingerprint') or ''})"
            if item is not None
            else "runtime failure circuit half-open: one Planner turn to queue a canary"
        )
        return True

    def _runtime_failure_canary_facts(self) -> dict[str, Any]:
        """Facts whose change is worth one canary of an open circuit.

        A backend or model switch saved from the cockpit, and new operator
        input, are new evidence about a failure no code change has fixed yet.
        Empty when unreadable: then only the timed window applies.
        """
        try:
            from ...core.knob_store import read_persisted_knobs
            from ...core.operator_context import (
                OperatorContextStore,
                operator_context_state_root,
            )

            return {
                "persisted_knobs": read_persisted_knobs(),
                "operator_context_revision": OperatorContextStore(
                    operator_context_state_root(self.memory)
                ).revision,
            }
        except Exception:  # noqa: BLE001 - unreadable facts open no window
            return {}

    def _close_runtime_failure_circuit(
        self, *, reason: str, canary_item_id: str = "", outage_only: bool = False,
    ) -> bool:
        """Close the circuit after a call got through; whether one closed.

        Any answer from the model service ends a provider outage, and the work
        the outage fenced goes back to the queue. A failure in Argus's own code
        closes only on a reviewed canary pass (``outage_only`` false).
        """
        from ..runtime_failure_circuit import PROVIDER_OUTAGE, clear_runtime_failure_circuit

        root = self.memory.root
        try:
            if not clear_runtime_failure_circuit(
                root, reason=reason, fingerprint=PROVIDER_OUTAGE,
                canary_item_id=canary_item_id,
            ):
                return not outage_only and clear_runtime_failure_circuit(
                    root, reason=reason, canary_item_id=canary_item_id,
                )
            resumed = self.memory.backlog.resume_parked({"provider_fence"})
        except Exception:  # noqa: BLE001 - the circuit stays open for the next window
            log.exception("failed to close the runtime failure circuit")
            return False
        if resumed:
            # Ready work: the daemon comes back for it instead of backing off.
            self._reset_idle_backoff()
        self._tell_status("service_back")
        return True

    def _execution_host_failure_block(
        self, *, item: Any | None = None,
    ) -> dict[str, Any] | None:
        """Hold work while the Planner's own host could not start.

        A new model session cannot restore a missing executable; re-enabling
        continuous work retries it. A task whose host could not start parks
        only itself until an explicit resume.
        """
        from ...daemon.state import read_continuous_state

        memory_root = (
            getattr(self.memory, "project_root", None)
            or getattr(self.memory, "root", None)
        )
        if memory_root is None:
            return None
        continuous = read_continuous_state(memory_root)
        if (
            not continuous.enabled
            and is_execution_host_startup_error(continuous.done_reason)
        ):
            words = self._operator_words("planning_paused_host")
            reason = (
                words["text"]
                + (" 技术记录：" if self._operator_chinese() else " Technical record: ")
                + str(continuous.done_reason)
            )
            if self._should_journal_idle_repeat("execution_host_failure"):
                self._emit({
                    "type": "life.execution_host.blocked",
                    "kind": words["kind"],
                    "item_id": str(getattr(item, "id", "") or ""),
                    "reason": reason,
                    "operator_alert": True,
                    "recoverable": True,
                })
                self._emit_status(reason)
            return {
                "status": "runtime_block",
                "item_id": str(getattr(item, "id", "") or ""),
                "reason": reason,
                "recoverable": True,
            }
        return None

    def _drain_peer_inbox(self) -> None:
        """Process peer evidence separately from operator steering at a boundary."""
        callback = getattr(getattr(self.config, "user_inbox", None), "drain_peer", None)
        if not callable(callback):
            return
        stop = getattr(self.config, "stop_event", None)
        if stop is not None and stop.is_set():
            return
        try:
            callback(self._bound_manager, cancelled=stop.is_set if stop is not None else None)
        except Exception:  # noqa: BLE001 - durable peers retry at the next boundary
            log.warning("peer inbox remains pending", exc_info=True)

    def _operator_inbox_unread(self, exc: Exception) -> None:
        log.warning("operator inbox unread; planning goes on without it", exc_info=True)
        if getattr(self, "_operator_inbox_alerted", False):
            return
        self._operator_inbox_alerted = True
        self._emit({
            "type": EventType.OPERATOR_ALERT,
            "operator_alert": True,
            "reason": "operator_inbox_unread",
            "text": (
                f"Operator messages could not be read ({exc}); they stay queued and "
                "are delivered once the inbox reads again. Work goes on meanwhile."
            ),
        })

    def _drain_user_inbox(self, *, max_messages: int = 10) -> list[str]:
        """Pull all pending operator nudges from the configured inbox.

        Returns up to ``max_messages`` lines (oldest-first). Empty list
        if no inbox is configured, nothing is pending, or the inbox cannot
        be read now — a flaky bus must never break a mission. An unread
        message stays leased in the durable inbox and is delivered at a
        later boundary; the operator hears once that it is waiting.
        """
        self._drain_peer_inbox()
        cb = getattr(self.config, "user_inbox", None)
        if cb is None:
            return []
        receiver = getattr(cb, "operator_receiver", None)
        if receiver is not None:
            from ...core.file_lock import DEFAULT_FILE_LOCK_TIMEOUT_SECONDS, bounded_file_lock_wait
            from ...core.operator_context import _current_mission_id
            from ...core.run_gateway import current_run_interrupt_reason

            stop = getattr(self.config, "stop_event", None)

            def cancelled() -> bool:
                return bool((stop is not None and stop.is_set()) or current_run_interrupt_reason())

            def manager_factory():
                try:
                    return self._bound_manager()
                except Exception:
                    return None

            try:
                with bounded_file_lock_wait(
                    timeout_seconds=DEFAULT_FILE_LOCK_TIMEOUT_SECONDS, cancelled=cancelled,
                ):
                    mission_id = _current_mission_id(receiver.root)
                    received = receiver.receive(
                        manager=manager_factory, mission_id=mission_id,
                        limit=max_messages, cancelled=cancelled,
                    )
            except Exception as exc:  # noqa: BLE001 - read again at the next boundary
                if not cancelled():
                    self._operator_inbox_unread(exc)
                return []
            self._operator_inbox_alerted = False
            if received:
                self._emit({
                    "type": EventType.LIFE_INBOX_DRAINED,
                    "count": len(received), "messages": received,
                    "source": "supervisor_intake", "delivery_boundary": "durable_acceptance",
                })
            return received
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
                from ...core.operator_context import operator_context_state_root
                from ...manager.directive import record_operator_messages

                # Persistence must not depend on routing: a message that cannot
                # be classified right now is still recorded as plain steering.
                try:
                    manager = self._bound_manager()
                except Exception:  # noqa: BLE001
                    manager = None
                record_operator_messages(
                    operator_context_state_root(self.memory) or self.memory.root,
                    out,
                    manager=manager,
                )
            except Exception:  # noqa: BLE001 - inbox delivery remains fail-soft
                log.exception("could not persist operator steering ledger")
            self._emit({
                "type": EventType.LIFE_INBOX_DRAINED,
                "count": len(out),
                "messages": out,
            })
        return out

    def _settle_operator_inbox(self, messages: list[str]) -> None:
        receiver = getattr(getattr(self.config, "user_inbox", None), "operator_receiver", None)
        if receiver is not None:
            receiver.settle(messages)

    def _release_operator_inbox(self) -> None:
        receiver = getattr(getattr(self.config, "user_inbox", None), "operator_receiver", None)
        if receiver is not None:
            receiver.release_pending()

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
                self._release_operator_inbox()
                continue
            if isinstance(result, dict) and bool(result.get("resolved")):
                try:
                    self._settle_operator_inbox([message])
                finally:
                    self._release_operator_inbox()
                self._reset_idle_backoff()
                # The resolver consumed this message, but the next Planner cycle
                # still needs it as durable guidance. Keep an in-process carryover;
                # the item and transcript remain the restart-safe record.
                carryover = getattr(self, "_operator_guidance_carryover", None)
                if carryover is None:
                    carryover = []
                    self._operator_guidance_carryover = carryover
                if not getattr(message, "ephemeral", False):
                    carryover.append(message)
                self._emit_status(
                    "operator inbox resolved pending question "
                    f"for backlog item {getattr(item, 'id', '')}"
                )
                return True
            self._release_operator_inbox()
        return False

    def _decide_parked_questions(self) -> bool:
        """While the operator cannot be asked, the Manager answers parked cards.

        Each choice goes through the resolver an operator's answer takes and is
        recorded for the run report in the Manager's name. A card the Manager
        names no valid choice for, or whose choice cannot be applied, is asked
        again after an idle backoff, at most ``PROXY_DECISION_ATTEMPTS`` times;
        then its task ends in the Manager's name, the one choice every card
        offers it, rather than waiting for an operator who cannot be asked.
        Returns whether any card was resolved.
        """
        from ...core.autonomy import (
            operator_questions_can_be_asked,
            record_autonomous_assumption,
        )
        from ...core.operator_decision import selected_decision_text
        from ...manager.proxy_decision import ProxyChoice, decide_for_operator, end_option

        resolver = getattr(self.config, "pending_question_resolver", None)
        root = self._project_state_root()
        if not callable(resolver) or operator_questions_can_be_asked(root):
            return False
        why = "The operator cannot be asked now"
        resolved = False
        for item in self.memory.backlog.active():
            card = item.operator_decision
            if item.status != "parked" or card.get("status") != "pending":
                continue
            key = f"{card.get('id')}:r{card.get('revision', 1)}"
            attempts, retry_at = self._proxy_retry.get(key, (0, 0.0))
            if attempts >= PROXY_DECISION_ATTEMPTS or time.monotonic() < retry_at:
                continue
            choice = result = None
            try:
                choice = decide_for_operator(
                    self._bound_manager(),
                    card,
                    why=why,
                    context=f"Task: {item.title}\n{item.objective[:2000]}",
                    on_event=getattr(self.sink, "handle_event", None),
                )
                if choice is not None:
                    result = resolver(item, choice)
            except Exception:  # noqa: BLE001 - the card stays parked and is asked again
                log.exception("Manager could not decide parked card %s", key)
            if choice is None or not (isinstance(result, dict) and result.get("resolved")):
                self._proxy_retry[key] = (
                    attempts + 1,
                    time.monotonic()
                    + min(IDLE_BACKOFF_CAP_SECONDS, IDLE_BACKOFF_BASE_SECONDS * 2**attempts),
                )
                if attempts + 1 < PROXY_DECISION_ATTEMPTS:
                    self._emit_status(
                        f"The Manager has not yet decided “{item.title}” in the "
                        "operator's place; it will be asked again"
                    )
                    continue
                choice = ProxyChoice(
                    decision_id=str(card.get("id") or ""), option=end_option(card), note="",
                    reason=(
                        f"The Manager named no choice it could apply in {PROXY_DECISION_ATTEMPTS} "
                        "tries, and nobody else can decide while the operator cannot be asked"
                    ),
                    evidence_refs=(),
                )
                try:
                    result = resolver(item, choice)
                except Exception:  # noqa: BLE001 - the card stays as it is
                    log.exception("could not end task %s for its undecided card", item.id)
                    result = None
                if not (isinstance(result, dict) and result.get("resolved")):
                    continue
                from ...core.operator_messages import brief_title

                self._tell_operator(
                    f"proxy-undecided-{key}", "proxy_undecided", title=brief_title(item.title),
                )
            record_autonomous_assumption(
                root,
                item_id=item.id,
                conflict=str(card.get("question") or ""),
                reading=(
                    selected_decision_text(card, choice.option, choice.note)
                    + f" (why: {choice.reason})"
                ),
                source="manager_decision",
                key=key,
                evidence_refs=choice.evidence_refs,
            )
            resolved = True
        if resolved:
            self._reset_idle_backoff()
        return resolved

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
        # the project done.
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

    def _enter_idle_backoff(self) -> None:
        """Register one more no-work plan-cycle and its suggested sleep."""
        self._consecutive_idle_planner_cycles += 1
        if getattr(self, "_idle_since", None) is None:
            self._idle_since = time.monotonic()
        self._suggested_sleep_s = self._idle_backoff_seconds()

    def _enter_pause_backoff(self) -> None:
        """Back off a recoverable pause without starting the idle-exit clock."""
        self._consecutive_idle_planner_cycles += 1
        self._idle_since = None
        self._suggested_sleep_s = self._idle_backoff_seconds()

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
            from ...apps._inbox_delivery import unique_operator_messages

            carryover = getattr(self, "_operator_guidance_carryover", None)
            if carryover is None:
                carryover = []
                self._operator_guidance_carryover = carryover
            carryover[:] = unique_operator_messages([*carryover, *messages])
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
                        "final_submission_signature",
                        "manuscript_snapshot",
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
            state_root=self._project_state_root(),
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

        # Intake has already rendered this cycle's context. A late drain must
        # restart intake, not fall through to certification with that old view.
        messages = self._drain_user_inbox()
        if messages:
            from ...apps._inbox_delivery import unique_operator_messages

            self._operator_guidance_carryover = unique_operator_messages(
                list(getattr(self, "_operator_guidance_carryover", None) or [])
                + messages
            )
            self._reset_idle_backoff()
            return PLAN_RETRY

        current = self._open_ended_terminal_idle_signature()
        if current != self._last_open_ended_project_done_signature:
            self._last_open_ended_project_done_signature = ""
            return None

        self._enter_idle_backoff()
        self._emit({
            "type": EventType.LIFE_PLANNER_TERMINAL_IDLE,
            "cycle": self._planning_cycles,
            "reason": "open-ended certified terminal state unchanged since last planner verdict",
        })
        self._emit_status(
            "planner: certified terminal state unchanged; standing objective remains "
            "active, idling without planner call"
        )
        return PLAN_TERMINAL_IDLE


__all__ = ["IdleCycleMixin", "_idle_exit_seconds"]
