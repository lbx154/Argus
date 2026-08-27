"""Daemon boot lifecycle phases: ``run_forever``'s pre-supervisor phases.

Split out of ``daemon.life_worker`` so that module stays under the
maintainability line-count target. ``LifeWorkerBootMixin`` is mixed into
``LifeWorker`` by the facade module. The construction of the
``LifeSupervisor`` uses a call-time lazy import back into ``life_worker`` so
that ``monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor",
...)`` (used extensively by ``tests/daemon/test_life_worker.py``) keeps
working even though the method that constructs it now lives in this module.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

from ..core.runtime_env import configure_framework_python_env
from ..life.memory import GlobalMemory, LifeMemory, MemoryBundle, ProjectMemory
from ._life_worker_identity import (
    _apply_continuous_suppression,
    _daemon_objective_requires_stage_reset,
    _legacy_manager_handoff_identity,
    _read_manager_handoff_identity,
    _rearm_operator_drain_for_resume,
    _refresh_file_backed_objective_for_resume,
    _resume_matches_manager_handoff,
    _write_manager_handoff_identity,
)
from ._life_worker_runtime_context import (
    _build_supervisor_config,
    _DaemonSink,
    _runner_namespace,
)
from .handoff import _strip_git_config_injection
from .state import (
    compare_and_swap_continuous_config,
    continuous_mode_error,
    read_continuous_state,
    write_continuous_config,
)

log = logging.getLogger(__name__)


class _RunForeverState:
    """Mutable scratch state threaded through ``LifeWorker.run_forever``'s
    lifecycle phases (``_rf_*`` methods). Process-local, never persisted.
    """

    def __init__(self) -> None:
        # Set by ``_rf_build_memory_runner_sink``.
        self.cfg: Any = None
        self.mem: Any = None
        self.runtime_root: Any = None
        self.runner: Any = None
        self.sink: Any = None
        self.daemon_sink: Any = None

        # Set by ``_rf_resolve_continuous_boot_state``.
        self.resume_intent: bool = False
        self.boot: Any = None
        self.suppress: dict[str, Any] = {}
        self.latest_continuous_state: Any = None
        self.continuous_provider: Any = None
        self.init_continuous: bool = False
        self.init_objective: str = ""
        self.init_source_state: Any = None
        self.resume_has_manager_handoff: bool = False
        self.manager_handoff_resolved: bool = False
        self.handoff_failure: str = ""

        # Set by ``_rf_build_supervisor``.
        self.sup: Any = None
        self.supervisors: list[Any] = []


class LifeWorkerBootMixin:
    """``run_forever``'s boot phases, up through supervisor construction."""

    def run_forever(self) -> int:
        rf_state = _RunForeverState()
        self._rf_bootstrap_environment()
        rf_state.cfg = self.config
        readiness_result = self._rf_vault_preflight(rf_state)
        if readiness_result is not None:
            return readiness_result
        self._rf_build_memory_runner_sink(rf_state)
        self._rf_resolve_continuous_boot_state(rf_state)
        self._rf_manager_divide_on_boot(rf_state)
        self._rf_build_supervisor(rf_state)
        self._rf_start_services(rf_state)
        self._rf_main_loop(rf_state)
        return self._rf_shutdown(rf_state)

    def _rf_bootstrap_environment(self) -> None:
        """Set up process env vars (PATH/PYTHONPATH/CUDA/git-config) before
        any child shell is spawned.
        """
        self._install_signal_handlers()
        self._started_at = time.time()

        # Keep every child shell on the same framework interpreter even when
        # Argus was launched through a Windows console script without activating
        # its virtual environment first.
        configure_framework_python_env(prepend_python_path=True)
        if self.config.global_root is not None:
            os.environ["ARGUS_SKILL_HOME"] = str(self.config.global_root.resolve())

        self._rf_export_configured_backend()

        # Make the project's ``code/`` importable in every child shell so inline
        # scripts and ``code/*.py`` helpers can ``import benchmark_loaders`` /
        # ``import gpu_env`` without per-command ``PYTHONPATH=$PWD/code``
        # gymnastics — a recurring source of wasted engineer rounds. Appended
        # (not prepended) so it never shadows argus_skill or stdlib modules.
        if self.config.project_workdir is not None:
            _code_dir = str((self.config.project_workdir / "code").resolve())
            _pp_parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
            if _code_dir not in _pp_parts:
                _pp_parts.append(_code_dir)
                os.environ["PYTHONPATH"] = os.pathsep.join(_pp_parts)
            # Expose the project root so the in-process reviewer/planner (and the
            # engineer subprocess, which inherits this env) resolve the SAME root
            # for the per-project harness overlay (.argus/harness/). The daemon
            # itself runs at cwd=/, so bare Path.cwd() would be wrong here.
            os.environ["ARGUS_SKILL_PROJECT_ROOT"] = str(self.config.project_workdir.resolve())

        # Strip the env-based ``GIT_CONFIG_*`` config-injection family from the
        # env handed to child codex shells. The codex sandbox forwards an
        # incomplete tuple (drops ``GIT_CONFIG_KEY_0``) that breaks *every*
        # ``git`` command in the agent's shell. See _strip_git_config_injection.
        _strip_git_config_injection(os.environ)

        # Set CUDA_VISIBLE_DEVICES from GPU resource allocation
        from ..tools.capability_vault import gpu_env_vars

        for k, v in gpu_env_vars().items():
            os.environ[k] = v

    def _rf_export_configured_backend(self) -> None:
        """Make ``--backend`` authoritative for the whole role-resolution chain.

        ``--backend`` is a CLI ARGUMENT. It lands in
        ``LifeWorkerConfig.backend`` and was never exported, so
        ``core.knobs.resolve_role_backend`` — which the reviewer gate, the
        subagent supervisor and the vault preflight all read — walked role env
        -> ``ARGUS_SKILL_RUNNER_BACKEND`` -> ``ARGUS_SKILL_LIFE_BACKEND`` ->
        persisted knob, found NOTHING (``/proc/<pid>/environ`` of a daemon
        launched with ``--backend copilot`` carries ``ARGUS_SKILL_HOME`` and no
        ``ARGUS_SKILL_*_BACKEND`` at all), and fell through to codex. A copilot
        campaign therefore ran its REVIEWER on ``codex`` against a relay with no
        token in the daemon environment: 401 ``Missing bearer`` on every review,
        and the paper hard-gated at ``model_review_unavailable``. The only
        workaround was hand-writing ``ARGUS_SKILL_REVIEWER_BACKEND`` into each
        project's ``state/config.json``.

        This runs FIRST in ``_rf_bootstrap_environment``, before the vault
        preflight and before any role resolves, so every later reader sees it.

        ``setdefault`` semantics, not assignment: an operator who explicitly
        exported ``ARGUS_SKILL_RUNNER_BACKEND`` before launching still outranks
        the flag, which is the precedence every other knob in this codebase
        uses. Spelled out rather than using ``os.environ.setdefault`` so the
        case where the flag LOSES can be logged instead of vanishing.

        ``memory`` is deliberately never exported: it is the in-process test
        backend, not an agent-CLI backend, and it is not in
        ``SUPPORTED_BACKENDS`` — exporting it would make
        ``normalize_runner_backend`` reject the whole chain.
        """
        configured = str(getattr(self.config, "backend", "") or "").strip()
        if not configured or configured.lower() == "memory":
            return
        existing = str(os.environ.get("ARGUS_SKILL_RUNNER_BACKEND", "") or "").strip()
        if existing:
            # The operator's export outranks the flag — but silently ignoring a
            # `--backend` they typed is precisely the class of thing this whole
            # change exists to stop. Say which one won.
            if existing.lower() != configured.lower():
                log.warning(
                    "daemon: ARGUS_SKILL_RUNNER_BACKEND=%s is already exported; "
                    "it outranks the configured backend %s for role resolution",
                    existing,
                    configured,
                )
            return
        os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = configured
        # Without this line the provenance recorded by _rf_record_role_backends
        # reads "env:ARGUS_SKILL_RUNNER_BACKEND" for a value that actually came
        # from the launch flag, and nothing would connect the two.
        log.info(
            "daemon: exported ARGUS_SKILL_RUNNER_BACKEND=%s from the configured "
            "backend so every role resolves to it",
            configured,
        )

    def _rf_build_memory_runner_sink(self, rf_state: _RunForeverState) -> None:
        """Open memory, build the runner, and wire the persistent event sink."""
        rf_state.cfg = self.config
        split_memory = bool(rf_state.cfg.global_root and rf_state.cfg.project_fingerprint)
        if split_memory:
            global_mem = GlobalMemory.open(rf_state.cfg.global_root)
            project_mem = ProjectMemory.open(
                rf_state.cfg.project_fingerprint,
                label=rf_state.cfg.project_label or rf_state.cfg.project_fingerprint,
                global_root=rf_state.cfg.global_root,
            )
            rf_state.mem = MemoryBundle(
                global_mem=global_mem,
                project=project_mem,
                project_worktree=rf_state.cfg.project_workdir,
            )
            rf_state.runtime_root = rf_state.mem.project.root
        else:
            rf_state.mem = LifeMemory.open(rf_state.cfg.life_dir)
            rf_state.runtime_root = rf_state.cfg.life_dir
        rf_state.mem.init()
        if split_memory:
            os.environ["ARGUS_SKILL_SESSION_ID"] = rf_state.cfg.project_fingerprint
        os.environ["ARGUS_SKILL_SESSION_ROOT"] = str(rf_state.runtime_root)
        os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(rf_state.runtime_root / "events.jsonl")

        # Build the rf_state.runner through the shared runtime composition root. Importing here
        # keeps daemon.life_worker free of CLI-only deps until needed.
        from ..apps._runtime import build_life_runner

        ns = _runner_namespace(rf_state.cfg)
        ns.stop_event = self._mission_stop
        rf_state.runner = build_life_runner(ns)

        # Continuous drain: each LifeSupervisor.run() drains until the
        # backlog goes empty or the budget caps. Then we sleep
        # poll_interval seconds and try again — items may have been
        # submitted from a coexisting cockpit.
        from ..life.event_log import JsonlEventSink
        from .health import DaemonHealthTracker

        # events.jsonl is the single persistent timeline.
        rf_state.daemon_sink = _DaemonSink(
            self,
            health_tracker=DaemonHealthTracker(rf_state.cfg.life_dir),
        )
        rf_state.sink = JsonlEventSink(
            rf_state.daemon_sink,
            life_dir=rf_state.runtime_root,
            verbosity=getattr(rf_state.cfg, "event_log_verbosity", "signal"),
        )
        self._rf_record_role_backends(rf_state)

    #: Roles whose backend the daemon resolves independently. Kept next to the
    #: emit below rather than imported from core.role_config: that tuple is the
    #: /roles DISPLAY set and omits the Curator, which the daemon does run.
    _BACKEND_PROVENANCE_ROLES: tuple[str, ...] = (
        "manager",
        "planner",
        "engineer",
        "reviewer",
        "curator",
    )

    def _rf_record_role_backends(self, rf_state: _RunForeverState) -> None:
        """Record which backend each role resolved to, and WHY, once per boot.

        Every consumer of a role's backend resolves it lazily and independently,
        so until now nothing anywhere stated the answer. When the reviewer ran
        on codex under a ``--backend copilot`` campaign, no log line, artifact
        or task record said so — the failure only surfaced downstream as a 401
        and a ``model_review_unavailable`` gate. These five events put the
        resolved value and its provenance (``env:<VAR>`` / ``persisted:<VAR>`` /
        ``default``) in ``events.jsonl`` at boot, where ``/roles`` and the
        cockpit can read it.
        """
        from ..core.event_catalog import EventType, new_event
        from ..core.knobs import (
            BackendResolutionError,
            resolve_role_backend_with_source,
        )

        for role in self._BACKEND_PROVENANCE_ROLES:
            try:
                backend, source = resolve_role_backend_with_source(
                    role,
                    default=str(rf_state.cfg.backend or "") or None,
                )
            except BackendResolutionError:
                # Unreachable while cfg.backend is set (it is the default we
                # pass), so this only fires for a config with an empty backend.
                # Provenance is a diagnostic: it must not be the thing that
                # stops a daemon from booting.
                log.exception("daemon: could not resolve %s backend for provenance", role)
                continue
            rf_state.sink.append(
                new_event(
                    EventType[f"LIFE_{role.upper()}_BACKEND_RESOLVED"],
                    role=role,
                    backend=backend,
                    source=source,
                    text=f"{role} backend resolved to {backend} ({source})",
                )
            )

    def _rf_resolve_continuous_boot_state(self, rf_state: _RunForeverState) -> None:
        """Resolve the boot-time continuous config, suppression, and the live
        ``continuous_provider`` the supervisor polls each cycle.
        """
        requested_open_ended = rf_state.cfg.continuous_open_ended
        # A fresh (non-resume) daemon must NOT adopt the project's persisted
        # continuous campaign — the operator manages daemons, and a daemon that
        # was not asked to resume has no business silently continuing a campaign
        # an earlier launch armed. Suppress a stale enabled-at-boot campaign
        # (leaving its on-disk state intact) unless this launch opted to resume
        # (--continuous / --resume-continuous) or the operator re-arms it live.
        rf_state.resume_intent = bool(
            rf_state.cfg.continuous or getattr(rf_state.cfg, "resume_continuous", False)
        )
        rf_state.boot = read_continuous_state(rf_state.runtime_root)
        rf_state.boot = _rearm_operator_drain_for_resume(
            cfg=rf_state.cfg,
            runtime_root=rf_state.runtime_root,
            state=rf_state.boot,
        )
        _refresh_file_backed_objective_for_resume(
            cfg=rf_state.cfg,
            runtime_root=rf_state.runtime_root,
            state=rf_state.boot,
        )
        rf_state.suppress = {
            "active": bool(rf_state.boot.enabled) and not rf_state.resume_intent,
            "objective": (rf_state.boot.objective or "").strip(),
            "generation": rf_state.boot.generation,
        }
        if rf_state.suppress["active"]:
            log.warning(
                "daemon: NOT resuming this project's persisted continuous campaign "
                "(objective=%r) — this launch did not opt in. Use --resume-continuous "
                "to auto-resume, or --continuous --objective to re-arm. Campaign "
                "state left intact.",
                rf_state.suppress["objective"][:80],
            )

        # Build a config provider that reads continuous.json from disk,
        # so the cockpit can enable/disable continuous mode while the daemon
        # is running — no daemon restart needed. A suppressed stale-boot
        # campaign stays off until the operator re-arms it (any change from the
        # boot state lifts the suppression and is then honored live).
        rf_state.latest_continuous_state = rf_state.boot

        def _continuous_provider() -> tuple[bool, str, bool]:
            current = read_continuous_state(rf_state.runtime_root)
            rf_state.latest_continuous_state = current
            enabled, objective = current.enabled, current.objective
            enabled, objective = _apply_continuous_suppression(
                rf_state.suppress,
                enabled,
                objective,
                generation=current.generation,
            )
            if continuous_mode_error(rf_state.cfg.backend, enabled, objective):
                if enabled:
                    write_continuous_config(
                        rf_state.runtime_root,
                        enabled=False,
                        objective=objective,
                    )
                return False, "", current.open_ended
            if not self._operator_stop_requested:
                self._adopted_continuous_generation = current.generation if enabled else None
            # A disabled record keeps its objective on disk so the operator can
            # inspect or explicitly re-arm it later. It must not seed the live
            # supervisor, or a paused/completed handoff can be treated as the
            # next continuous objective during daemon resume.
            return enabled, (objective if enabled else ""), current.open_ended

        rf_state.continuous_provider = _continuous_provider

        # Seed continuous config from disk (or CLI flags).
        (
            rf_state.init_continuous,
            rf_state.init_objective,
            rf_state.cfg.continuous_open_ended,
        ) = rf_state.continuous_provider()
        rf_state.init_source_state = rf_state.latest_continuous_state
        if rf_state.cfg.continuous:
            rf_state.cfg.continuous_open_ended = requested_open_ended
            # CLI flags override disk. Persist only after Manager has produced a
            # role-clean execution handoff.
            rf_state.init_continuous = True
            rf_state.init_objective = rf_state.cfg.continuous_objective or rf_state.init_objective

        # ``resume_continuous`` adopts a campaign only when its objective and
        # vertical match a durable Manager handoff identity. This avoids a fresh
        # provider dependency on every crash recovery / upgrade without trusting
        # a torn or unrelated PIPELINE_STATE write.
        rf_state.resume_has_manager_handoff = (
            rf_state.init_continuous
            and _resume_matches_manager_handoff(
                cfg=rf_state.cfg,
                runtime_root=rf_state.runtime_root,
                state=rf_state.init_source_state,
                objective=str(rf_state.init_objective or ""),
            )
        )
        if rf_state.resume_has_manager_handoff:
            rf_state.manager_handoff_resolved = True
            log.info(
                "daemon boot: adopting persisted Manager handoff for continuous generation %d",
                rf_state.init_source_state.generation,
            )

    def _rf_manager_divide_on_boot(self, rf_state: _RunForeverState) -> None:
        """Reset the Manager's codex session, then classify + persist the
        vertical via ``Manager.divide`` before the supervisor starts.
        """
        # New daemon = fresh isolation generation: drop the Manager's persistent
        # codex session so it does NOT resume the PRIOR daemon's accumulated
        # conversation. Runs BEFORE the boot divide() so even boot classification
        # starts clean. Fail-open. / 新 daemon = 全新隔离代际：清掉 Manager 的常驻
        # codex 会话，不 resume 上一个 daemon 的累积对话；放在 boot divide() 之前，
        # 连启动分类也从干净会话开始；失败也不阻塞启动。
        try:
            from ..manager import reset_manager_session as _reset_mgr_session

            _mgr_session_root = rf_state.runtime_root
            if _mgr_session_root and _reset_mgr_session(_mgr_session_root):
                log.info(
                    "daemon boot: cleared prior Manager codex session at %s",
                    _mgr_session_root,
                )
        except Exception:  # noqa: BLE001 — never block daemon start on session reset
            pass
        # Manager divides the task before the supervisor starts — same as the
        # foreground path (apps/_runtime.run_life_supervisor): classify the vertical,
        # split into Stages, and commit it so the supervisor trusts the persisted
        # vertical. A missing handoff fails closed: raw operator text never reaches
        # Planner/Engineer.
        if (
            rf_state.init_continuous
            and str(rf_state.init_objective or "").strip()
            and not rf_state.suppress["active"]
            and not rf_state.resume_has_manager_handoff
        ):
            source_objective = str(rf_state.init_objective).strip()
            expected_state = rf_state.init_source_state
            intent_id = f"intent-daemon-{time.time_ns()}"
            rf_state.sink.append(
                {
                    "type": "life.manager.intent.started",
                    "agent_layer": "manager",
                    "intent_id": intent_id,
                    "source": "daemon_boot",
                    "objective": source_objective,
                    "text": "manager interpreting daemon objective",
                }
            )
            try:
                mgr = rf_state.runner.manager
                if mgr is None:
                    raise RuntimeError("runner was constructed without a Manager")
                from ..manager.front_door import (
                    require_manager_execution_task,
                )
                from ..skills.vertical_select import (
                    _persisted_domain,
                    _persisted_vertical,
                )

                decision = mgr.decide_vertical(source_objective)
                execution_task = require_manager_execution_task(decision)
                prior_vertical = _persisted_vertical(
                    rf_state.runtime_root
                )
                prior_domain = _persisted_domain(
                    rf_state.runtime_root
                )
                prior_handoff = _read_manager_handoff_identity(rf_state.runtime_root)
                if prior_handoff is None and prior_vertical:
                    prior_handoff = _legacy_manager_handoff_identity(
                        rf_state.runtime_root,
                        objective=expected_state.objective,
                        vertical=prior_vertical,
                        domain=prior_domain or "",
                    )
                prior_vertical_name = str(prior_vertical or "").strip()
                next_vertical_name = str(getattr(decision, "vertical", "") or "").strip()
                next_domain_name = str(getattr(decision, "domain", "") or "").strip()
                replacement_intent = _daemon_objective_requires_stage_reset(
                    project_root=rf_state.runtime_root,
                    prior_vertical=prior_vertical_name,
                    next_vertical=next_vertical_name,
                    prior_domain=str(prior_domain or ""),
                    next_domain=next_domain_name,
                    prior_handoff=prior_handoff,
                    expected_objective=expected_state.objective,
                    source_objective=source_objective,
                    execution_task=execution_task,
                )
                if self._operator_stop_requested:
                    raise RuntimeError("operator stop requested during Manager handoff")
                target_enabled = True if rf_state.cfg.continuous else expected_state.enabled
                committed: dict[str, Any] = {}

                def _commit_decision() -> None:
                    committed["division"] = mgr.commit_vertical_decision(
                        source_objective,
                        decision,
                        ask_on_new_domain=False,
                        force_stage_reset=replacement_intent,
                        _lock_held=True,
                    )
                    if replacement_intent:
                        supersede = getattr(
                            rf_state.mem.backlog,
                            "supersede_pending_for_replacement",
                            None,
                        )
                        if callable(supersede):
                            committed["superseded_ids"] = supersede(
                                reason=("operator replaced the standing Manager objective"),
                                replacement_id=intent_id,
                            )

                lock_factory = getattr(mgr, "pipeline_lock", None)
                pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
                with pipeline_lock:
                    swapped = compare_and_swap_continuous_config(
                        rf_state.runtime_root,
                        expected=expected_state,
                        enabled=target_enabled,
                        objective=execution_task,
                        open_ended=rf_state.cfg.continuous_open_ended,
                        before_write=_commit_decision,
                    )
                if swapped:
                    division = committed["division"]
                    rf_state.init_continuous = target_enabled
                    rf_state.init_objective = execution_task
                    if not self._operator_stop_requested:
                        self._adopted_continuous_generation = (
                            expected_state.generation + 1 if target_enabled else None
                        )
                    completed_event = {
                        "type": "life.manager.intent.completed",
                        "agent_layer": "manager",
                        "intent_id": intent_id,
                        "item_id": intent_id,
                        "source": "daemon_boot",
                        "continuous_generation": expected_state.generation + 1,
                        "objective": source_objective,
                        "execution_task": execution_task,
                        "vertical": getattr(division, "vertical", ""),
                        "route": "team",
                        "workflow_mode": getattr(division, "workflow_mode", ""),
                        "lifetime": (
                            "standing"
                            if target_enabled and rf_state.cfg.continuous_open_ended
                            else "bounded"
                        ),
                        "continuous": target_enabled,
                        "open_ended": (
                            target_enabled and rf_state.cfg.continuous_open_ended
                        ),
                        "domain": getattr(division, "domain", ""),
                        "kind": getattr(division, "kind", ""),
                        "learned_vertical_status": getattr(
                            division,
                            "learned_vertical_status",
                            "",
                        ),
                        "stages": list(getattr(division, "stages", []) or []),
                        "reason": str(
                            getattr(decision, "adaptation_reason", "") or ""
                        ).strip(),
                        "text": "manager completed daemon objective handoff",
                    }
                    try:
                        live_stage = str(mgr.current_stage() or "").strip()
                    except Exception:  # noqa: BLE001 - event enrichment is best effort
                        live_stage = ""
                    if live_stage:
                        completed_event["current_stage"] = live_stage
                    rf_state.sink.append(completed_event)
                    rf_state.manager_handoff_resolved = True
                    for item_id in committed.get("superseded_ids", ()):
                        rf_state.sink.append(
                            {
                                "type": "life.plan.node.superseded",
                                "item_id": item_id,
                                "superseded_by_plan_id": intent_id,
                                "reason": ("operator replaced the standing Manager objective"),
                                "source": "manager_intent_replacement",
                            }
                        )
                    _write_manager_handoff_identity(
                        rf_state.runtime_root,
                        objective=execution_task,
                        vertical=str(getattr(division, "vertical", "") or ""),
                        domain=str(getattr(division, "domain", "") or ""),
                        continuous_generation=expected_state.generation + 1,
                        intent_id=intent_id,
                        source_objective=source_objective,
                        source_objective_path=str(
                            getattr(
                                rf_state.cfg,
                                "continuous_objective_file",
                                "",
                            )
                            or ""
                        ),
                    )
                else:
                    if (
                        read_continuous_state(rf_state.runtime_root).generation
                        == expected_state.generation
                    ):
                        rf_state.init_continuous, rf_state.init_objective = False, ""
                        if expected_state.enabled:
                            rf_state.suppress.update(
                                {
                                    "active": True,
                                    "objective": expected_state.objective,
                                    "generation": expected_state.generation,
                                }
                            )
                        rf_state.sink.append(
                            {
                                "type": "life.manager.intent.failed",
                                "agent_layer": "manager",
                                "intent_id": intent_id,
                                "source": "daemon_boot",
                                "error": "failed to persist Manager execution handoff",
                                "text": "manager daemon objective handoff was not persisted",
                            }
                        )
                        rf_state.handoff_failure = (
                            "failed to persist Manager execution handoff"
                        )
                    else:
                        (
                            rf_state.init_continuous,
                            rf_state.init_objective,
                            rf_state.cfg.continuous_open_ended,
                        ) = rf_state.continuous_provider()
                        rf_state.sink.append(
                            {
                                "type": "life.manager.intent.superseded",
                                "agent_layer": "manager",
                                "intent_id": intent_id,
                                "source": "daemon_boot",
                                "text": "daemon objective changed during Manager handoff",
                            }
                        )
                rf_state.cfg.continuous_objective = rf_state.init_objective
            except Exception as exc:  # noqa: BLE001 — fail closed, keep daemon available
                current_state = read_continuous_state(rf_state.runtime_root)
                if current_state.generation == expected_state.generation:
                    rf_state.init_continuous = False
                    rf_state.init_objective = ""
                    if current_state.enabled:
                        rf_state.suppress.update(
                            {
                                "active": True,
                                "objective": current_state.objective,
                                "generation": current_state.generation,
                            }
                        )
                else:
                    (
                        rf_state.init_continuous,
                        rf_state.init_objective,
                        rf_state.cfg.continuous_open_ended,
                    ) = rf_state.continuous_provider()
                rf_state.cfg.continuous_objective = rf_state.init_objective
                log.error("daemon Manager handoff failed; objective not dispatched: %s", exc)
                rf_state.handoff_failure = f"{type(exc).__name__}: {exc}"
                rf_state.sink.append(
                    {
                        "type": "life.manager.intent.failed",
                        "agent_layer": "manager",
                        "intent_id": intent_id,
                        "source": "daemon_boot",
                        "objective": source_objective,
                        "error": f"{type(exc).__name__}: {exc}",
                        "text": "manager daemon objective handoff failed",
                    }
                )

    def _rf_build_supervisor(self, rf_state: _RunForeverState) -> None:
        """Construct the ``LifeSupervisor`` after Manager vertical selection."""
        # Build supervisor policy only AFTER Manager.divide() has persisted the
        # vertical.  Mission typing is fail-safe (non-paper until a
        # ``certified`` vertical is positively resolved), so constructing this
        # before divide would incorrectly leave a brand-new paper campaign in
        # bounded mode for its whole daemon lifetime.
        sup_cfg = _build_supervisor_config(
            rf_state.cfg,
            runtime_root=rf_state.runtime_root,
            stop_event=self._stop,
            init_continuous=rf_state.init_continuous,
            init_objective=rf_state.init_objective,
            continuous_provider=rf_state.continuous_provider,
            post_mission_hook=self._deployment_handoff_after_mission,
        )
        sup_cfg.planner_cycle_gate = self._deployment_handoff_gate

        # Lazy proxy: resolve ``LifeSupervisor`` through the facade module's
        # OWN namespace at call time (not this module's), so
        # ``monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", ...)``
        # in tests still takes effect even though this method now lives here.
        from .life_worker import LifeSupervisor

        refresh_skill_store = getattr(
            rf_state.runner,
            "_refresh_manager_skill_store",
            None,
        )
        if callable(refresh_skill_store):
            refresh_skill_store(
                rf_state.runner._args,
                workdir=rf_state.cfg.project_workdir,
            )
        rf_state.sup = LifeSupervisor(
            memory=rf_state.mem,
            runner=rf_state.runner,
            sink=rf_state.sink,
            config=sup_cfg,
            engineer_model=rf_state.cfg.engineer_model,
            reviewer_model=rf_state.cfg.reviewer_model,
            planner_runner=getattr(rf_state.runner, "planner_backend", None)
            or getattr(rf_state.runner, "backend", None),
            skill_store=getattr(rf_state.runner, "_manager_skill_store", None),
        )
        if rf_state.manager_handoff_resolved:
            rf_state.sup._vertical_resolved = True
        effective_width = (
            0
            if rf_state.cfg.mission_width == 0
            else (1 if rf_state.cfg.backend == "memory" else rf_state.cfg.mission_width)
        )
        if effective_width == 0:
            rf_state.supervisors = []
            return
        rf_state.supervisors = [rf_state.sup]
        if effective_width == 1:
            return
        rf_state.sup.config.coordinate_parallel_claims = True

        from ..apps._runtime import build_life_runner

        helper_cfg = replace(
            sup_cfg,
            continuous=False,
            continuous_objective="",
            continuous_config_provider=None,
            planner_cycle_gate=None,
            post_mission_hook=None,
            user_inbox=None,
            parallel_worker=True,
            holds_stage_authority=False,
        )
        for index in range(1, effective_width):
            ns = _runner_namespace(rf_state.cfg)
            ns.stop_event = self._mission_stop
            helper_runner = build_life_runner(ns)
            refresh_helper_skill_store = getattr(
                helper_runner,
                "_refresh_manager_skill_store",
                None,
            )
            if callable(refresh_helper_skill_store):
                refresh_helper_skill_store(
                    helper_runner._args,
                    workdir=rf_state.cfg.project_workdir,
                )
            worker_config = replace(
                helper_cfg,
                worker_id=f"parallel-{index}",
            )
            rf_state.supervisors.append(
                LifeSupervisor(
                    memory=rf_state.mem,
                    runner=helper_runner,
                    sink=rf_state.sink,
                    config=worker_config,
                    engineer_model=rf_state.cfg.engineer_model,
                    reviewer_model=rf_state.cfg.reviewer_model,
                    # A helper that can plan an idle slot needs a planner to ask,
                    # the same one the primary uses.
                    planner_runner=getattr(helper_runner, "planner_backend", None)
                    or getattr(helper_runner, "backend", None),
                    skill_store=getattr(helper_runner, "_manager_skill_store", None),
                )
            )
