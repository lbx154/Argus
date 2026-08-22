"""Daemon run lifecycle phases: self-maintenance/vault preflight through the
main drain loop and shutdown, plus their supporting helpers.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. ``LifeWorkerRunMixin`` is mixed into
``LifeWorker`` by the facade module.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ._life_worker_boot import _RunForeverState
from ._life_worker_identity import _effective_runner_backend, _worker_vault_preflight_routes
from .state import (
    GRACEFUL_STOP_REASON,
    clear_daemon_control_stop,
    clear_daemon_drain_request,
    compare_and_swap_continuous_config,
    read_continuous_state,
)

log = logging.getLogger(__name__)


class LifeWorkerRunMixin:
    """``run_forever``'s post-boot phases: self-maintenance, main loop, shutdown."""

    def _rf_init_self_maintenance(self, rf_state: _RunForeverState) -> int | None:
        """Construct self-maintenance and resolve any pending rollback/resume
        handoff. Returns ``0`` when a handoff was spawned and
        ``run_forever`` should exit immediately, else ``None``.
        """
        # Lazy proxy: resolve through the facade module's OWN namespace at
        # call time so `monkeypatch.setattr(life_worker, "_spawn_handoff_candidate", ...)`
        # still takes effect even though this method now lives here.
        from .life_worker import _spawn_handoff_candidate

        self._self_maintenance = None
        maintenance_enabled = os.environ.get(
            "ARGUS_SKILL_SELF_MAINTENANCE",
            "1",
        ).strip().lower() not in {"0", "false", "no", "off"}
        if (
            maintenance_enabled
            and rf_state.cfg.backend != "memory"
            and rf_state.cfg.project_workdir
        ):
            try:
                from ..core.runtime_identity import (
                    source_revision,
                    source_root,
                )
                from .self_maintenance import DaemonSelfMaintenance

                self._self_maintenance = DaemonSelfMaintenance(
                    life_dir=rf_state.runtime_root,
                    framework_root=source_root(),
                    project_workdir=rf_state.cfg.project_workdir,
                    manager=rf_state.runner.manager,
                    memory=rf_state.mem,
                    backend=rf_state.cfg.backend,
                    on_event=rf_state.sink.handle_event,
                )
                rf_state.daemon_sink.self_maintenance = self._self_maintenance
                self._self_maintenance.preflight_isolation(force=True)
                self._self_maintenance.prune_obsolete_worktrees()
                self._self_maintenance.mark_canary_started(
                    loaded_source_root=source_root(),
                    revision=str(source_revision() or ""),
                )
                failed_canary_rollback = self._self_maintenance.failed_start_rollback_candidate(
                    loaded_source_root=source_root(),
                )
                if failed_canary_rollback is not None:
                    if _spawn_handoff_candidate(
                        self.config,
                        reason=(
                            "loaded self-maintenance source failed reviewed commit "
                            "identity; restore prior runtime"
                        ),
                        candidate_source_root=failed_canary_rollback,
                    ):
                        self._stop.set()
                        return 0
                    self._self_maintenance.mark_handoff_failed(
                        "canary identity failed and rollback did not reach standby"
                    )
                resume_source = self._self_maintenance.source_resume_candidate(
                    loaded_source_root=source_root(),
                )
                if resume_source is not None:
                    if _spawn_handoff_candidate(
                        self.config,
                        reason=(
                            "restore this daemon's persisted self-managed runtime "
                            "after process restart"
                        ),
                        candidate_source_root=resume_source,
                        rollback_source_root=source_root(),
                    ):
                        self._stop.set()
                        return 0
                    self._self_maintenance.mark_handoff_failed(
                        "persisted self-managed runtime did not reach standby"
                    )
            except Exception:  # noqa: BLE001 - research remains available
                log.exception("daemon: self-maintenance initialization failed")
        return None

    def _rf_vault_preflight(self, rf_state: _RunForeverState) -> int | None:
        """Validate backend/auth before constructing providers or mutating state."""
        from ..core.runtime_identity import release_match_preflight_error

        release_error = release_match_preflight_error()
        if release_error:
            log.error("daemon refused inconsistent release: %s", release_error)
            return 2
        from ..core.backend_readiness import (
            check_backend_readiness,
            format_backend_readiness,
        )

        if str(rf_state.cfg.backend or "").strip().lower() == "memory":
            return None
        skip_vault_probe = (
            os.environ.get("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "").strip() == "1"
        )
        if skip_vault_probe:
            log.warning(
                "UNSAFE diagnostic override: skipping model-api network probe; "
                "backend/auth/config validation remains enabled"
            )
        readiness = check_backend_readiness(
            rf_state.cfg.backend,
            probe_auth=True,
            probe_vault=not skip_vault_probe,
            required_routes=_worker_vault_preflight_routes(rf_state.cfg.backend),
        )
        if not readiness.ok:
            log.error(
                "daemon refused before Manager/provider/state mutation:\n%s",
                format_backend_readiness(readiness),
            )
            return 2
        return None

    def _rf_start_services(self, rf_state: _RunForeverState) -> None:
        """Log readiness, start the Telegram poller, and start the Curator."""
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

        # Start the resident Curator: it keeps each active team campaign's pool
        # in flight and is the single reaper (the lead drops .argus/team campaign
        # markers under project_workdir, which the Curator watches). Stopped in
        # the finally below so a clean shutdown reaps every teammate it owns.
        self._curator = self._build_curator(rf_state.runner)
        if self._curator is not None:
            self._curator.start()

    def _rf_main_loop(self, rf_state: _RunForeverState) -> None:
        """Drain the backlog until stop is requested, running the
        self-maintenance canary/rollback/audit checks and the wakeable
        poll-interval sleep between drains.
        """
        # Lazy proxy: see ``_rf_init_self_maintenance`` above for why this
        # cannot be a top-level import.
        from .life_worker import _spawn_handoff_candidate

        try:
            while not self._stop.is_set():
                summary: dict = {}
                try:
                    from ..manager._session_ops import manager_pipeline_yield_requested

                    if manager_pipeline_yield_requested(rf_state.runtime_root):
                        self._stop.wait(0.2)
                        continue
                    manager = getattr(rf_state.runner, "manager", None)
                    lock_factory = getattr(manager, "pipeline_lock", None)
                    pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
                    with pipeline_lock:
                        supervisors = getattr(
                            rf_state,
                            "supervisors",
                            [rf_state.sup],
                        )
                        if not supervisors:
                            summary = {
                                "stopped_by": "paused_workers",
                                "suggested_sleep": rf_state.cfg.poll_interval,
                            }
                        elif len(supervisors) == 1:
                            summary = rf_state.sup.run()
                        else:
                            with ThreadPoolExecutor(
                                max_workers=len(supervisors),
                                thread_name_prefix="argus-mission",
                            ) as executor:
                                futures = [
                                    executor.submit(supervisor.run)
                                    for supervisor in supervisors
                                ]
                                summary = futures[0].result()
                                for future in futures[1:]:
                                    future.result()
                        # Persist the planner's terminal decision before any
                        # optional self-maintenance. A maintenance handoff may
                        # rewrite stopped_by or raise; neither may resurrect a
                        # campaign the Planner already completed.
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
                        if self._self_maintenance is not None:
                            pr_result = self._self_maintenance.reconcile_pull_request()
                            if pr_result.startswith("rollback:"):
                                rollback_root = Path(pr_result.removeprefix("rollback:"))
                                if rollback_root.is_dir() and _spawn_handoff_candidate(
                                    self.config,
                                    reason=(
                                        "self-maintenance PR closed without "
                                        "merge; restore prior runtime"
                                    ),
                                    candidate_source_root=rollback_root,
                                ):
                                    self._stop.set()
                                    summary["stopped_by"] = "daemon_handoff"
                                    continue
                                self._self_maintenance.mark_handoff_failed(
                                    "closed PR rollback did not reach standby"
                                )
                            maintenance_action = self._self_maintenance.audit_if_due(
                                daemon_state={
                                    "summary": summary,
                                    "continuous_enabled": bool(
                                        read_continuous_state(rf_state.runtime_root).enabled
                                    ),
                                    "project_workdir": str(rf_state.cfg.project_workdir or ""),
                                    "budget_allowed": bool(
                                        rf_state.sup.config.budget.can_start(
                                            global_root=rf_state.cfg.global_root,
                                        )[0]
                                    ),
                                }
                            )
                            if maintenance_action.startswith("adopt:"):
                                candidate_root = Path(maintenance_action.removeprefix("adopt:"))
                                from ..core.runtime_identity import source_root

                                if _spawn_handoff_candidate(
                                    self.config,
                                    reason=(
                                        "this daemon's Manager approved a "
                                        "human-merged framework update"
                                    ),
                                    candidate_source_root=candidate_root,
                                    rollback_source_root=source_root(),
                                ):
                                    self._stop.set()
                                    summary["stopped_by"] = "daemon_handoff"
                                else:
                                    self._self_maintenance.mark_handoff_failed(
                                        "approved upstream canary did not reach standby"
                                    )
                    if self._self_maintenance is not None:
                        canary_result = self._self_maintenance.publish_after_canary(summary=summary)
                        if canary_result.startswith("rollback:"):
                            rollback_root = Path(canary_result.removeprefix("rollback:"))
                            if rollback_root.is_dir() and _spawn_handoff_candidate(
                                self.config,
                                reason=(
                                    "self-maintenance canary failed its explicit "
                                    "health check; restore prior runtime"
                                ),
                                candidate_source_root=rollback_root,
                            ):
                                self._stop.set()
                                summary["stopped_by"] = "daemon_handoff"
                            else:
                                self._self_maintenance.mark_handoff_failed(
                                    "canary failed and rollback did not reach standby"
                                )
                    # A bounded campaign owns exactly one terminal objective.
                    # The supervisor has already persisted project_done and the
                    # maintenance hooks above have had their one clean handoff
                    # opportunity, so another drain pass can only re-open a
                    # completed project and waste tokens. Open-ended daemons keep
                    # their resident behavior unchanged.
                    if (
                        summary.get("stopped_by") == "project_done"
                        and not rf_state.cfg.continuous_open_ended
                    ):
                        log.info("daemon: bounded project completed; exiting cleanly")
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
                # Reset per-run counters so future drain passes work.
                for supervisor in (
                    getattr(rf_state, "supervisors", None) or [rf_state.sup]
                ):
                    supervisor._missions_started = 0
                    supervisor._planning_cycles = 0
                if self._stop.is_set():
                    break
                # Honor the supervisor's suggested backoff (escalating while it is
                # idle awaiting an external dependency). The sleep is wakeable: it returns
                # early on stop, or when the user inbox grows — so /add and /nudge
                # stay responsive even during a long await-external backoff.
                try:
                    suggested = float(summary.get("suggested_sleep") or 0.0)
                except Exception:  # noqa: BLE001
                    suggested = 0.0
                self._wakeable_sleep(
                    max(float(rf_state.cfg.poll_interval), suggested),
                    rf_state.cfg.poll_interval,
                    rf_state.runtime_root,
                )
        finally:
            if self._curator is not None:
                self._curator.stop()
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

    def _rf_shutdown(self, rf_state: _RunForeverState) -> int:
        """Quiesce continuous mode on an operator stop and log the final
        uptime/mission-count summary.
        """
        # Operator clock-out (别干了): a graceful stop (SIGTERM/SIGINT set
        # self._stop — including a bare ``kill`` and ``--daemon-stop``) quiesces
        # continuous mode so the campaign does NOT silently resurrect on the next
        # daemon launch. A crash (SIGKILL / power loss) never reaches here, so
        # continuous stays enabled and the campaign auto-resumes — the intended
        # crash-recovery.
        if self._operator_stop_requested:
            self._quiesce_continuous_on_operator_stop(
                rf_state.runtime_root,
                self._adopted_continuous_generation,
            )

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
                done_reason=GRACEFUL_STOP_REASON,
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
    ) -> None:
        """Sleep up to ``total_seconds``, waking early on stop or new inbox input.

        The sleep is chunked into ``poll_interval`` slices so a stop request or
        a freshly ``/add``'d / ``/nudge``'d message (which appends to the
        project ``inbox.jsonl``) interrupts a long backoff promptly.
        """
        if total_seconds <= 0:
            return
        chunk = max(0.5, float(poll_interval))
        inbox = Path(runtime_root) / "inbox.jsonl"
        offset_file = Path(runtime_root) / "inbox.offset"

        def _inbox_size() -> int:
            try:
                return inbox.stat().st_size
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

        baseline = _inbox_size()
        if _inbox_offset() < baseline:
            return
        remaining = float(total_seconds)
        while remaining > 0 and not self._stop.is_set():
            self._stop.wait(timeout=min(chunk, remaining))
            if self._stop.is_set():
                return
            if _inbox_size() != baseline:
                return  # new user input — re-drain immediately
            remaining -= chunk

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        """Canary an independently reviewed private self-maintenance change."""
        # Lazy proxy: see ``_rf_init_self_maintenance`` above for why this
        # cannot be a top-level import.
        from .life_worker import _spawn_handoff_candidate

        maintenance = getattr(self, "_self_maintenance", None)
        if maintenance is not None:
            publish_canary = getattr(maintenance, "publish_after_canary", None)
            if callable(publish_canary):
                canary_result = publish_canary(
                    summary={
                        "stopped_by": "",
                        "planning_cycles": 0,
                        "results": [outcome],
                    }
                )
                if canary_result.startswith("rollback:"):
                    rollback_root = Path(canary_result.removeprefix("rollback:"))
                    if rollback_root.is_dir() and _spawn_handoff_candidate(
                        self.config,
                        reason=(
                            "self-maintenance canary failed after a mission; "
                            "restore prior runtime"
                        ),
                        candidate_source_root=rollback_root,
                    ):
                        self._stop.set()
                        return "daemon_handoff"
                    maintenance.mark_handoff_failed(
                        "mission-level canary rollback did not reach standby"
                    )
                    return ""
            candidate_root = maintenance.prepare_reviewed_change(outcome)
            if candidate_root is not None:
                from ..core.runtime_identity import source_root

                if _spawn_handoff_candidate(
                    self.config,
                    reason=(
                        "independently reviewed self-maintenance change; "
                        "canary this daemon before PR publication"
                    ),
                    candidate_source_root=candidate_root,
                    rollback_source_root=source_root(),
                ):
                    self._stop.set()
                    return "daemon_handoff"
                maintenance.mark_handoff_failed("private canary did not reach standby")
        return ""
