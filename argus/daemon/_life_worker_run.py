"""Daemon run lifecycle phases: vault preflight through the main drain loop
and shutdown, plus their supporting helpers.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. ``LifeWorkerRunMixin`` is mixed into
``LifeWorker`` by the facade module.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..core import process_stop
from ..core.daemon_lock import is_pid_running
from ._life_worker_boot import _RunForeverState
from ._life_worker_identity import _effective_runner_backend
from .state import (
    GRACEFUL_STOP_REASON,
    HOST_SHUTDOWN_STOP_REASON,
    clear_daemon_control_stop,
    clear_daemon_drain_request,
    compare_and_swap_continuous_config,
    read_continuous_state,
)

log = logging.getLogger(__name__)


def _open_runtime_failure_circuit(supervisors: Any) -> dict[str, Any] | None:
    """The first active runtime failure circuit among the worker's supervisors."""
    from ..life.runtime_failure_circuit import active_runtime_failure_circuit

    for supervisor in supervisors or ():
        root = getattr(getattr(supervisor, "memory", None), "root", None)
        if root is None:
            continue
        try:
            circuit = active_runtime_failure_circuit(root)
        except Exception:  # noqa: BLE001 - an unreadable advisory must not stop work
            log.debug("could not inspect runtime failure circuit", exc_info=True)
            continue
        if circuit is not None:
            return circuit
    return None

_RUNNING_STALL_ERROR = "executor exited without completing the task"
_RUNNING_STALL_POLL_SECONDS = 1.0
_HELPER_WAKE_CHECK_SECONDS = 0.5
_HELPER_WAKE_SETTLE_SECONDS = 0.05


def _backlog_fingerprint(supervisor: Any) -> tuple | None:
    """Cheap change marker for the backlog a supervisor claims from."""
    backlog = getattr(getattr(supervisor, "memory", None), "backlog", None)
    paths = getattr(backlog, "storage_paths", None)
    if not paths:
        return None
    marks: list[tuple[int, int, int] | None] = []
    for path in paths:
        try:
            stat = Path(path).stat()
        except OSError:
            marks.append(None)
            continue
        marks.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return tuple(marks)


def _maybe_consolidate(rf_state: Any, config: Any) -> None:
    """Consolidate this project's vertical knowledge when its pages changed.

    Called after every drain pass, before the sleep. Skips quietly when
    learning is off, the daemon has no project workspace, the vertical is
    undecided, or the shared root has nothing new; ``consolidate_knowledge``
    compares page file facts with its receipt first, so the common path costs
    one stat per page. Never raises into the loop.
    """
    try:
        from ..core.knobs import learning_enabled

        if not learning_enabled():
            return
        workspace = getattr(config, "project_workdir", None)
        sup = getattr(rf_state, "sup", None)
        runner = getattr(rf_state, "runner", None)
        if workspace is None or sup is None or runner is None:
            return
        from ..skills.vertical_select import resolve_project_vertical

        try:
            vertical = str(resolve_project_vertical(workspace, life_dir=config.life_dir) or "").strip()
        except Exception:  # noqa: BLE001 - an undecided vertical only skips this pass
            log.debug("daemon: knowledge consolidation skipped; no vertical", exc_info=True)
            return
        if not vertical:
            return
        root_of = getattr(sup, "_budget_global_root", None)
        global_root = (
            root_of()
            if callable(root_of)
            else getattr(getattr(rf_state, "mem", None), "global_root", None)
        )
        if not isinstance(global_root, (str, Path)):
            return
        from ..life.consolidation import consolidate_knowledge

        consolidate_knowledge(
            runner=runner,
            global_root=Path(global_root),
            life_dir=Path(config.life_dir),
            vertical=vertical,
            emit=getattr(sup, "_emit", None),
        )
    except Exception:  # noqa: BLE001 - consolidation never stops the drain loop
        log.exception("daemon: knowledge consolidation failed")


class LifeWorkerRunMixin:
    """``run_forever``'s post-boot phases: main loop and shutdown."""

    # Constructed by LifeWorker; every run/lock wait observes this same signal.
    _stop: threading.Event
    # Set by LifeWorker at construction (a LifeWorkerConfig).
    config: Any

    @staticmethod
    def _backlog_item(rf_state: _RunForeverState, item_id: str) -> Any | None:
        return next(
            (
                candidate
                for candidate in rf_state.mem.backlog.history()
                if candidate.id == item_id
            ),
            None,
        )

    def _record_runtime_recovery(
        self,
        rf_state: _RunForeverState,
        *,
        detector: str,
        invariant: str,
        item: Any,
        observed: dict[str, Any],
        action: str,
        expected_postcondition: str,
        recovered: bool,
        verification: dict[str, Any],
    ) -> None:
        from ..core.runtime_incidents import (
            RuntimeIncidentStore,
            drain_runtime_incident_events,
        )

        try:
            store = RuntimeIncidentStore(rf_state.runtime_root)
            incident = store.detect(
                detector=detector,
                invariant=invariant,
                subject_kind="mission",
                subject_id=str(item.id),
                severity="error",
                observed=observed,
            )
            store.begin_recovery(
                incident["incident_id"],
                action=action,
                expected_postcondition=expected_postcondition,
            )
            store.verify_recovery(
                incident["incident_id"],
                recovered=recovered,
                evidence=verification,
            )
            drain_runtime_incident_events(
                rf_state.runtime_root,
                rf_state.sink.handle_event,
            )
        except Exception:  # noqa: BLE001 - backlog recovery remains authoritative
            log.exception(
                "daemon: failed to record runtime recovery for mission %s",
                item.id,
            )

    def _recover_stalled_running_items(self, rf_state: _RunForeverState) -> list[str]:
        """Requeue running claims whose executor thread died.

        A dead executor says nothing about the mission itself, so the item keeps
        its workspace and goes back to the queue for the next free worker.
        """
        from ..life.role_activity import role_activity

        if self._supervisor_execution_active.is_set():
            return []

        now = time.time()
        activities = role_activity(rf_state.runtime_root, now=now)
        if any(activity.active for activity in activities.values()):
            return []

        executor_threads = self._supervisor_execution_threads
        recovered: list[str] = []
        for item in rf_state.mem.backlog.active():
            if item.status != "running" or item.started_ts is None:
                continue
            owner = str(getattr(item, "running_owner", "") or "") or "primary"
            executor_thread = executor_threads.get(owner)
            if executor_thread is None or executor_thread.is_alive():
                continue
            if rf_state.mem.backlog.update(
                item.id,
                status="pending",
                attempt=max(1, int(item.attempt or 1)) + 1,
                started_ts=None,
                finished_ts=None,
                running_owner="",
                last_error=_RUNNING_STALL_ERROR,
            ) is None:
                continue
            settled = self._backlog_item(rf_state, str(item.id))
            self._record_runtime_recovery(
                rf_state,
                detector="running_stall_watchdog",
                invariant="running_requires_live_executor",
                item=item,
                observed={"backlog_status": "running", "executor_alive": False},
                action="requeue_running_item",
                expected_postcondition="parent mission is pending and claimable",
                recovered=bool(settled is not None and settled.status == "pending"),
                verification={"backlog_status": getattr(settled, "status", "")},
            )
            recovered.append(item.id)
            log.warning("daemon: requeued stalled running item %s", item.id)
        return recovered

    def _run_supervisor_pass(self, supervisor: Any) -> dict:
        worker_id = str(
            getattr(getattr(supervisor, "config", None), "worker_id", "primary")
            or "primary"
        )
        self._supervisor_execution_threads[worker_id] = threading.current_thread()
        try:
            return supervisor.run()
        finally:
            self._supervisor_execution_threads.pop(worker_id, None)
            # Per-run counters start again with the worker's next pass.
            supervisor._missions_started = 0
            supervisor._planning_cycles = 0

    def _start_helpers(
        self, supervisors: list[Any],
    ) -> tuple[Callable[[], dict], list[threading.Thread]]:
        """Start each helper on its own loop; return the primary pass and the helpers.

        A helper that finds nothing claimable looks again (after a wakeable
        wait) while a primary pass runs, so items the primary plans or
        resumes mid-pass are taken up in parallel instead of the primary
        chaining them one after another. Nothing waits for a helper's pass:
        a long mission there carries on while the primary replans and passes
        again on its own schedule. Once the worker stops, each helper ends
        after the pass it is in.
        """
        if len(supervisors) < 2:
            return (lambda: self._run_supervisor_pass(supervisors[0])), []
        poll = float(getattr(getattr(self, "config", None), "poll_interval", 5.0) or 5.0)
        primary_pass = threading.Event()
        resume_lock = threading.Lock()
        last_resume = [float("-inf")]

        def resume_for_cycle() -> None:
            # One resume per dispatch cycle, ahead of every worker's claim;
            # the backlog write wakes the idle helpers so each resumed mission
            # goes to a free worker instead of waiting for the one that woke it.
            with resume_lock:
                now = time.monotonic()
                if now - last_resume[0] < min(poll, 1.0):
                    return
                last_resume[0] = now
                resume = getattr(supervisors[0], "_resume_automatic_pauses", None)
                if not callable(resume):
                    return
                try:
                    resume()
                except Exception:  # noqa: BLE001 - a failed resume retries next cycle
                    log.exception("daemon: resuming paused missions failed")

        def run_primary() -> dict:
            resume_for_cycle()
            primary_pass.set()
            try:
                return self._run_supervisor_pass(supervisors[0])
            finally:
                primary_pass.clear()

        def helper_loop(supervisor: Any) -> None:
            while not self._stop.is_set():
                resume_for_cycle()
                baseline = _backlog_fingerprint(supervisor)
                try:
                    result = self._run_supervisor_pass(supervisor)
                except Exception:  # noqa: BLE001 - like a primary pass, retry after a poll
                    log.exception("daemon: helper pass raised; retrying")
                    result = {"suggested_sleep": poll}
                # Honour the supervisor's own backoff (it escalates for
                # holds, pauses and lost claims, and each idle pass journals
                # a status event), but wake early once the backlog changes:
                # the primary planning, claiming or resuming an item writes
                # the backlog, and that may have made work claimable.
                suggested = result.get("suggested_sleep") if isinstance(result, dict) else 0
                wait = max(float(suggested or 0), min(poll, 1.0))
                self._wait_for_helper_wake(
                    supervisor, wait, poll=poll, primary_pass=primary_pass, baseline=baseline,
                )

        for supervisor in supervisors:
            supervisor._dispatcher_resumes = True
        helpers = [
            threading.Thread(
                # One copied context per helper: a single Context cannot be
                # entered concurrently.
                target=contextvars.copy_context().run,
                args=(helper_loop, supervisor),
                name=f"argus-mission-{index}",
            )
            for index, supervisor in enumerate(supervisors[1:], 1)
        ]
        for helper in helpers:
            helper.start()
        return run_primary, helpers

    def _wait_for_helper_wake(
        self,
        supervisor: Any,
        wait: float,
        *,
        poll: float,
        primary_pass: threading.Event,
        baseline: tuple | None,
    ) -> None:
        """Sleep ``wait`` seconds and on until a primary pass runs, or until stop.

        Returns early when the backlog changed since ``baseline`` (so a newly
        ready item is claimed promptly). Between primary passes an idle helper
        sleeps on: it looked for work while the last one ran, and the next one
        or any backlog write wakes it. The checks are file stats only, so
        watching for a change never journals anything.
        """
        deadline = time.monotonic() + wait
        slice_s = max(0.01, min(_HELPER_WAKE_CHECK_SECONDS, poll))
        while not self._stop.is_set():
            current = _backlog_fingerprint(supervisor)
            if current != baseline:
                # Let an in-progress write (truncate-then-write, multi-row
                # append) settle first, so the helper's next baseline sees the
                # finished state and one change wakes it once, not twice.
                while not self._stop.wait(_HELPER_WAKE_SETTLE_SECONDS):
                    settled = _backlog_fingerprint(supervisor)
                    if settled == current or time.monotonic() >= deadline:
                        break
                    current = settled
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0 and primary_pass.is_set():
                return
            self._stop.wait(min(slice_s, remaining) if remaining > 0 else slice_s)

    def _start_running_stall_watcher(self, rf_state: _RunForeverState) -> None:
        def _watch() -> None:
            while not self._running_stall_stop.wait(_RUNNING_STALL_POLL_SECONDS):
                try:
                    self._recover_stalled_running_items(rf_state)
                except Exception:  # noqa: BLE001 - watchdog failure must not stop work
                    log.exception("daemon: stalled-running watchdog failed")

        self._running_stall_thread = threading.Thread(
            target=_watch,
            name="argus-running-stall",
            daemon=True,
        )
        self._running_stall_thread.start()

    def _stop_running_stall_watcher(self) -> None:
        self._running_stall_stop.set()
        if self._running_stall_thread is not None:
            self._running_stall_thread.join(timeout=2.0)

    @staticmethod
    def _drain_runtime_incidents(rf_state: _RunForeverState) -> int:
        from ..core.runtime_incidents import drain_runtime_incident_events

        return drain_runtime_incident_events(
            rf_state.runtime_root,
            rf_state.sink.handle_event,
        )

    def _deployment_handoff_gate(self) -> str:
        from ..core.runtime_identity import source_root
        from .handoff import _consume_deployment_handoff, _record_handoff_alert
        from .life_worker import _spawn_handoff_candidate

        candidate = _consume_deployment_handoff(Path(self.config.life_dir))
        if candidate is None or candidate == source_root().resolve():
            return ""
        successor = _spawn_handoff_candidate(
            self.config,
            reason="operator adopted a reviewed framework deployment",
            candidate_source_root=candidate,
            rollback_source_root=source_root(),
        )
        if not successor:
            _record_handoff_alert(
                self.config,
                f"The adopted framework deployment ({candidate}) was dropped: its "
                "runtime did not reach handoff standby, so the daemon keeps "
                "running the current runtime. The new runtime's boot log says why.",
                reason="daemon_handoff_standby_failed",
            )
            return ""
        # Shutdown checks this pid is still waiting before releasing the lock.
        self._handoff_successor_pid = successor if type(successor) is int else 0
        # Drain for real. Helper missions run on ``_mission_stop`` and the
        # lead's harness waits watch only the process-wide stop flag; with just
        # ``_stop`` set they kept this process (and the daemon lock) busy for
        # as long as those missions ran. Interrupted missions park on
        # ``daemon_shutdown``, which the successor resumes.
        # Not ``request_process_stop``: a handoff is not an operator clock-out.
        process_stop.request_stop()
        self._mission_stop.set()
        self._stop.set()
        return "daemon_handoff"

    def _replace_lost_handoff_successor(self) -> None:
        """Never leave the project without a daemon after a handoff.

        The successor waits for the lock for as long as this process lives, so
        it is gone here only if it crashed or was killed; it would otherwise
        leave nobody once this process exits. Restart this runtime in its
        place; the deployment it carried is dropped, and the operator is told.
        A replacement that cannot reach standby either runs this same code, so
        it is not retried blindly: the operator is told no daemon is left.
        """
        successor = int(getattr(self, "_handoff_successor_pid", 0) or 0)
        if successor <= 0 or self._operator_stop_requested:
            return
        if is_pid_running(successor):
            return
        from ..core.runtime_identity import source_root
        from .handoff import _record_handoff_alert
        from .life_worker import _spawn_handoff_candidate

        self._handoff_successor_pid = 0
        dropped = (
            f"The adopted framework deployment was dropped: its runtime (pid "
            f"{successor}) exited before taking over. "
        )
        if _spawn_handoff_candidate(
            self.config,
            reason="handoff successor exited before takeover; keep current runtime",
            candidate_source_root=source_root(),
        ):
            _record_handoff_alert(
                self.config,
                dropped + "The current runtime is taking over in its place.",
                reason="daemon_handoff_successor_lost",
            )
            return
        _record_handoff_alert(
            self.config,
            dropped + "The current runtime did not restart either: no daemon is "
            "running for this project. Start it again with `argus --daemon`.",
            reason="daemon_handoff_replacement_failed",
        )

    def _deployment_handoff_after_mission(self, _outcome: object) -> str:
        return self._deployment_handoff_gate()

    def _rf_preflight(self, rf_state: _RunForeverState) -> int | None:
        """Refuse an inconsistent install before constructing providers or mutating state.

        The backend itself is not probed here (no login, version or model-API
        check): like the TypeScript runtime, a backend that cannot serve fails
        on its first real call, where the error reaches the project.
        """
        # An unreadable knob file is checked HERE, before anything resolves a
        # role. Every resolver reads that file, so without this the first
        # resolution to run — somewhere deep in role construction — raises
        # instead, and the operator gets a traceback whose top frame has nothing
        # to do with the actual fault. Refusing here is also the point: booting
        # anyway would silently revert every persisted switch at once (the
        # backend of every role, the model of every route).
        from ..core.knob_store import KnobStoreCorruptError, read_persisted_knobs
        from ..core.runtime_identity import (
            release_match_preflight_error,
            source_root_preflight_error,
        )

        release_error = release_match_preflight_error()
        if release_error:
            log.error("daemon refused inconsistent release: %s", release_error)
            return 2

        try:
            read_persisted_knobs()
        except KnobStoreCorruptError as exc:
            log.error("daemon refused before Manager/provider/state mutation: %s", exc)
            return 2

        # After the knob-store gate above: the configured root may live in
        # that same file, and a corrupt store must keep its own diagnosis.
        source_error = source_root_preflight_error()
        if source_error:
            log.error("daemon refused mismatched source root: %s", source_error)
            return 2

        return None

    def _rf_start_services(self, rf_state: _RunForeverState) -> None:
        """Log readiness and start the chat bridges."""
        if rf_state.handoff_failure:
            log.warning(
                "daemon: degraded; Manager objective was not dispatched "
                "(life_dir=%s backend=%s pid=%d error=%s)",
                rf_state.runtime_root,
                _effective_runner_backend(rf_state.runner, rf_state.cfg.backend),
                os.getpid(),
                rf_state.handoff_failure,
            )
            rf_state.sink.append(
                {
                    "type": "life.daemon.degraded",
                    "health": "degraded",
                    "objective_dispatched": False,
                    "error": rf_state.handoff_failure,
                    "text": "daemon started without dispatching the Manager objective",
                }
            )
        else:
            log.info(
                "daemon: ready (life_dir=%s backend=%s pid=%d)",
                rf_state.runtime_root,
                _effective_runner_backend(rf_state.runner, rf_state.cfg.backend),
                os.getpid(),
            )
            tracker = getattr(rf_state.daemon_sink, "health_tracker", None)
            if tracker is not None:
                try:
                    tracker.mark_ready()
                except Exception:  # noqa: BLE001 - health telemetry is non-critical
                    log.exception("daemon: failed to mark health ready")

        # Start the Telegram inbound command poller only when explicitly enabled.
        try:
            from ..life.telegram_bot import telegram_enabled

            if telegram_enabled():
                from ..life.telegram_bot import TelegramPoller

                tg_poller = TelegramPoller(
                    life_dir=rf_state.runtime_root,
                    stop_event=self._stop,
                )
                tg_poller.start()
            else:
                log.info("telegram poller disabled")
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to start telegram poller; continuing")

        # Same contract for Feishu/Lark: opt-in, and a failure here must never
        # take down a daemon that is otherwise healthy.
        try:
            from ..life.feishu_bot import feishu_enabled

            if feishu_enabled():
                from ..life.feishu_bot import FeishuPoller

                fs_poller = FeishuPoller(
                    life_dir=rf_state.runtime_root,
                    stop_event=self._stop,
                )
                fs_poller.start()
            else:
                log.info("feishu bridge disabled")
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to start feishu bridge; continuing")

        # Protect a resumed running claim before the main loop reaches its first
        # supervisor call. The loop clears this guard as soon as that call
        # returns, which is the only point where executor-loss detection is safe.
        self._supervisor_execution_active.set()
        self._start_running_stall_watcher(rf_state)

    def _rf_main_loop(self, rf_state: _RunForeverState) -> None:
        """Drain the backlog until stop is requested, sleeping wakeably."""

        from ..core.autonomy import start_autonomy_run

        # Each worker is one run: assumptions and operator-only blocks from an
        # earlier run of this project are not reported again.
        start_autonomy_run()
        from ..core import call_guard

        call_guard.enable(
            on_trip=lambda labels: self._background_calls_paused(rf_state, labels)
        )
        supervisors = getattr(rf_state, "supervisors", [rf_state.sup])
        run_primary, helpers = self._start_helpers(supervisors)
        try:
            while not self._stop.is_set():
                if self._deployment_handoff_gate():
                    break
                try:
                    self._drain_runtime_incidents(rf_state)
                except Exception:  # noqa: BLE001 - incidents remain durable for retry
                    log.exception("daemon: runtime incident delivery remains pending")
                summary: dict = {}
                self._supervisor_execution_active.set()
                try:
                    if not supervisors:
                        summary = {
                            "stopped_by": "paused_workers",
                            "suggested_sleep": rf_state.cfg.poll_interval,
                        }
                    else:
                        summary = run_primary()
                    if summary.get("stopped_by") == "project_done":
                        current = read_continuous_state(rf_state.runtime_root)
                        if (
                            current.enabled
                            and self._adopted_continuous_generation is not None
                            and current.generation
                            == self._adopted_continuous_generation
                            and compare_and_swap_continuous_config(
                                rf_state.runtime_root,
                                expected=current,
                                enabled=False,
                                objective=current.objective,
                                done_reason="planner declared project done",
                            )
                        ):
                            self._adopted_continuous_generation = None
                    # A bounded worker owns one finite queue. A plain backlog
                    # finishes with ``backlog_empty``; a finite campaign ends
                    # with ``project_done``. Both must release the process slot.
                    # A standing campaign that was enabled while this worker was
                    # alive remains resident even if the launch itself was bounded.
                    terminal_bounded_stop = summary.get("stopped_by") in {
                        "backlog_empty",
                        "project_done",
                    }
                    standing = read_continuous_state(rf_state.runtime_root)
                    standing_enabled = standing.enabled and standing.open_ended
                    if (
                        terminal_bounded_stop
                        and not rf_state.cfg.continuous_open_ended
                        and not standing_enabled
                    ):
                        from ..life.observer import settle

                        # The delivery did not wait for the Observer; the
                        # process waits for its look at the last turns.
                        settle(self._stop)
                        log.info("daemon: bounded work completed; exiting cleanly")
                        break
                    # A bounded worker parked behind an open runtime failure
                    # circuit waits on facts this process does not produce (a
                    # changed runtime, the operator's Run). Release the slot
                    # instead of idling until killed.
                    if (
                        not rf_state.cfg.continuous_open_ended
                        and not standing_enabled
                    ):
                        circuit = _open_runtime_failure_circuit(supervisors)
                        if circuit is not None:
                            log.warning(
                                "daemon: bounded work blocked by an open runtime failure "
                                "circuit (%s); exiting",
                                circuit.get("fingerprint") or "unknown",
                            )
                            break
                    # Idle auto-exit: the supervisor judged the project idle past
                    # the cap. Exit the loop so the process shuts down cleanly
                    # (the shutdown distillation below runs) — the session model
                    # respawns this daemon on the operator's next --resume.
                    if summary.get("stopped_by") == "idle_timeout":
                        log.info(
                            "daemon: idle-timeout reached; exiting cleanly (resume to continue)"
                        )
                        break
                except Exception:  # noqa: BLE001
                    if self._stop.is_set():
                        log.info("daemon: drain pass interrupted by stop request")
                        break
                    log.exception("daemon: drain pass raised; sleeping and retrying")
                finally:
                    self._supervisor_execution_active.clear()
                log.info(
                    "daemon: drain pass stopped_by=%s suggested_sleep=%s",
                    summary.get("stopped_by") or "",
                    summary.get("suggested_sleep") or 0,
                )
                if self._stop.is_set():
                    break
                # Between passes the host sleeps on what it learned, when the
                # shared knowledge changed; cheap otherwise.
                _maybe_consolidate(rf_state, self.config)
                # Honor the supervisor's suggested backoff (escalating while it is
                # idle awaiting an external dependency). The sleep is wakeable: it returns
                # early on stop, when the user inbox grows, or when someone else
                # writes the backlog — so /add, /nudge and an explicit Run stay
                # responsive even during a long await-external backoff.
                try:
                    suggested = float(summary.get("suggested_sleep") or 0.0)
                except (AttributeError, TypeError, ValueError):
                    suggested = 0.0
                self._wakeable_sleep(
                    max(float(rf_state.cfg.poll_interval), suggested),
                    rf_state.cfg.poll_interval,
                    rf_state.runtime_root,
                    supervisor=rf_state.sup,
                )
        finally:
            from ..manager.supervision import shutdown_supervision

            # Each helper ends after the pass it is in; a drain keeps its mission.
            self._stop.set()
            for helper in helpers:
                helper.join()
            call_guard.disable()
            shutdown_supervision(rf_state.runtime_root)
            self._stop_running_stall_watcher()
            if self._control_started_at_iso:
                clear_daemon_control_stop(
                    self.config.life_dir,
                    pid=os.getpid(),
                    started_at_iso=self._control_started_at_iso,
                )
            clear_daemon_drain_request(
                self.config.life_dir,
                pid=os.getpid(),
            )

    @staticmethod
    def _background_calls_paused(rf_state: _RunForeverState, labels: list[str]) -> None:
        """Tell the operator, once per trip, that background calls stopped."""
        import re

        from ..core.call_guard import BACKGROUND_CALL_BUDGET
        from ..core.mission_view import say, uses_cjk
        from ..core.operator_messages import publish_operator_message

        supervisor = rf_state.sup
        # Which calls they were is the developer's record, not the operator's.
        kinds = ", ".join(sorted({re.sub(r"\d+", "N", label) for label in labels}))[:300]
        publish_operator_message(
            supervisor._manager_project_report_root(),
            text=say(
                "background_calls_paused",
                uses_cjk(getattr(supervisor.config, "continuous_objective", "")),
                calls=BACKGROUND_CALL_BUDGET,
            ),
            message_id=f"background-calls-paused-{int(time.time())}",
            event_fields={"background_calls_paused": True},
        )
        supervisor._emit_status(
            f"background model calls paused after {BACKGROUND_CALL_BUDGET} calls "
            f"without progress ({kinds})"
        )

    def _rf_shutdown(self, rf_state: _RunForeverState) -> int:
        """Quiesce continuous mode on an operator stop and log the final
        uptime/mission-count summary.
        """
        # Operator clock-out (别干了): a graceful stop (SIGTERM/SIGINT set
        # self._stop — including a bare ``kill`` and ``--daemon-stop``) quiesces
        # continuous mode so the campaign does NOT silently resurrect on the next
        # daemon launch. A crash (SIGKILL / power loss) never reaches here, so
        # continuous stays enabled and the campaign auto-resumes — the intended
        # crash-recovery. A SIGTERM no operator asked for (host shutdown) also
        # quiesces, under its own resumable reason, so only the next server
        # start resumes it and a bare relaunch does not.
        if self._operator_stop_requested:
            self._quiesce_continuous_on_operator_stop(
                rf_state.runtime_root,
                self._adopted_continuous_generation,
            )
        self._replace_lost_handoff_successor()

        log.info(
            "daemon: stopping cleanly (uptime=%.1fs missions=%d)",
            time.time() - (self._started_at or time.time()),
            self._missions_completed,
        )
        return 0

    def _quiesce_continuous_on_operator_stop(
        self,
        runtime_root: Path,
        adopted_generation: int | None,
    ) -> None:
        """Operator clock-out (别干了): disable continuous mode on a graceful stop.

        When the daemon is asked to stop (SIGTERM/SIGINT — including a bare
        ``kill`` and ``--daemon-stop``), it "clocks out": it flips
        ``continuous.json`` to ``enabled=false`` so the campaign stays stopped
        and does NOT silently resurrect when a fresh daemon is later launched on
        this project. The objective is preserved so the operator can re-arm.

        A crash (SIGKILL / OOM / power loss) never runs this path, so continuous
        stays enabled and the campaign auto-resumes — the intended crash
        recovery. No-op for a non-continuous daemon. Best-effort: never blocks
        shutdown.
        """
        if adopted_generation is None:
            return
        try:
            current = read_continuous_state(runtime_root)
            if not current.enabled or current.generation != adopted_generation:
                return
            if not compare_and_swap_continuous_config(
                runtime_root,
                expected=current,
                enabled=False,
                objective=current.objective,
                done_reason=(
                    HOST_SHUTDOWN_STOP_REASON
                    if getattr(self, "_host_shutdown", False)
                    else GRACEFUL_STOP_REASON
                ),
            ):
                return
            log.info("daemon: quiesced continuous mode on operator stop (clock out)")
        except Exception:  # noqa: BLE001 — quiesce is best-effort
            log.exception("daemon: failed to quiesce continuous on operator stop")

    def _wakeable_sleep(
        self,
        total_seconds: float,
        poll_interval: float,
        runtime_root: Path,
        *,
        supervisor: Any = None,
    ) -> None:
        """Sleep until stop, inbox input, a backlog write, or configuration changes.

        The sleep is chunked into ``poll_interval`` slices so a stop request or
        newly queued operator guidance interrupts a long backoff promptly. The
        pass that just ended wrote the backlog before the sleep began, so a
        change seen during it is someone else's new fact (an explicit Run that
        resumed parked work, an answer, a new task), and wakes the daemon the
        way a backlog write wakes an idle helper.
        """
        if total_seconds <= 0:
            return
        chunk = max(0.5, float(poll_interval))
        inbox = Path(runtime_root) / "inbox.jsonl"
        offset_file = Path(runtime_root) / "inbox.offset"
        from ..apps._inbox import count_pending_inbox_messages, latest_durable_inbox_timestamp
        from ..core.paths import config_path

        operator_config = config_path(self.config.global_root)

        def _config_version() -> tuple[int, int, int] | None:
            try:
                stat = operator_config.stat()
                return stat.st_ino, stat.st_mtime_ns, stat.st_size
            except OSError:
                return None

        def _inbox_size() -> int:
            try:
                return inbox.stat().st_size if inbox.is_file() else 0
            except OSError:
                return 0

        def _inbox_offset() -> int:
            try:
                return max(
                    0,
                    int(offset_file.read_text(encoding="utf-8").strip() or "0"),
                )
            except (OSError, ValueError):
                return 0

        def _durable_input() -> tuple[float | None, int]:
            try:
                return latest_durable_inbox_timestamp(runtime_root), count_pending_inbox_messages(runtime_root)
            except (OSError, RuntimeError):
                # Wake the ordinary intake path to report the required-state
                # failure; a read error must not look like an empty inbox.
                return None, 1

        baseline = _inbox_size()
        durable_baseline = _durable_input()
        config_baseline = _config_version()
        backlog_baseline = _backlog_fingerprint(supervisor)
        previously_observed = getattr(self, "_last_inbox_wake_observation", None)
        self._last_inbox_wake_observation = durable_baseline
        if _inbox_offset() < baseline or (durable_baseline[1] and durable_baseline != previously_observed):
            return
        remaining = float(total_seconds)
        while remaining > 0 and not self._stop.is_set():
            self._stop.wait(timeout=min(chunk, remaining))
            if self._stop.is_set():
                return
            if _inbox_size() != baseline:
                return  # new user input — re-drain immediately
            durable_now = _durable_input()
            if durable_now != durable_baseline:
                self._last_inbox_wake_observation = durable_now
                return
            if _config_version() != config_baseline:
                return  # an operator config change must wake paused work
            if _backlog_fingerprint(supervisor) != backlog_baseline:
                return
            remaining -= chunk
