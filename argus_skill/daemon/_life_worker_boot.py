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
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ..life.memory import GlobalMemory, LifeMemory, MemoryBundle, ProjectMemory
from ._life_worker_identity import (
    _apply_continuous_suppression,
    _daemon_objective_requires_stage_reset,
    _legacy_manager_handoff_identity,
    _read_manager_handoff_identity,
    _rearm_operator_drain_for_resume,
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

        # Set by ``_rf_build_supervisor``.
        self.sup: Any = None


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
        maintenance_result = self._rf_init_self_maintenance(rf_state)
        if maintenance_result is not None:
            return maintenance_result
        self._rf_start_services(rf_state)
        self._rf_main_loop(rf_state)
        return self._rf_shutdown(rf_state)

    def _rf_bootstrap_environment(self) -> None:
        """Set up process env vars (PATH/PYTHONPATH/CUDA/git-config) before
        any child shell is spawned.
        """
        self._install_signal_handlers()
        self._started_at = time.time()

        # Ensure ARGUS_SKILL_PYTHON is set in the process environment so
        # all child processes can find the argus_skill package. Without this,
        # shells spawned by codex exec fall back to /usr/bin/python which cannot
        # import argus_skill.
        _argus_python = os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable
        os.environ.setdefault("ARGUS_SKILL_PYTHON", _argus_python)
        if self.config.global_root is not None:
            os.environ["ARGUS_SKILL_HOME"] = str(self.config.global_root.resolve())
        # Also prepend the venv bin dir to PATH so bare `python` resolves
        # to the venv interpreter in child shells.
        _venv_bin = str(Path(_argus_python).resolve().parent)
        _current_path = os.environ.get("PATH", "")
        if _venv_bin not in _current_path:
            os.environ["PATH"] = f"{_venv_bin}:{_current_path}"

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

        # events.jsonl is the single persistent timeline.
        rf_state.daemon_sink = _DaemonSink(self)
        rf_state.sink = JsonlEventSink(
            rf_state.daemon_sink,
            life_dir=rf_state.runtime_root,
            verbosity=getattr(rf_state.cfg, "event_log_verbosity", "signal"),
        )

    def _rf_resolve_continuous_boot_state(self, rf_state: _RunForeverState) -> None:
        """Resolve the boot-time continuous config, suppression, and the live
        ``continuous_provider`` the supervisor polls each cycle.
        """
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

        def _continuous_provider() -> tuple[bool, str]:
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
                return False, objective
            if not self._operator_stop_requested:
                self._adopted_continuous_generation = current.generation if enabled else None
            return enabled, objective

        rf_state.continuous_provider = _continuous_provider

        # Seed continuous config from disk (or CLI flags).
        rf_state.init_continuous, rf_state.init_objective = rf_state.continuous_provider()
        rf_state.init_source_state = rf_state.latest_continuous_state
        if rf_state.cfg.continuous:
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
                # Prefer the rf_state.runner's single Manager instance (manager backend);
                # fall back to an ad-hoc Manager only when the rf_state.runner has none
                # (e.g. the memory rf_state.runner in tests).
                mgr = getattr(rf_state.runner, "manager", None)
                if mgr is None:
                    from ..manager import Manager

                    mgr = Manager(
                        project_root=rf_state.cfg.project_workdir or rf_state.runtime_root,
                        runner=getattr(rf_state.runner, "manager_backend", None)
                        or getattr(rf_state.runner, "backend", None),
                        manager_session_root=rf_state.runtime_root,
                    )
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
                    rf_state.cfg.project_workdir or rf_state.runtime_root
                )
                prior_domain = _persisted_domain(
                    rf_state.cfg.project_workdir or rf_state.runtime_root
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
                    project_root=rf_state.cfg.project_workdir or rf_state.runtime_root,
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
                        "domain": getattr(division, "domain", ""),
                        "kind": getattr(division, "kind", ""),
                        "stages": list(getattr(division, "stages", []) or []),
                        "text": "manager completed daemon objective handoff",
                    }
                    try:
                        live_stage = str(mgr.current_stage() or "").strip()
                    except Exception:  # noqa: BLE001 - event enrichment is best effort
                        live_stage = ""
                    if live_stage:
                        completed_event["current_stage"] = live_stage
                    rf_state.sink.append(completed_event)
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
                    else:
                        rf_state.init_continuous, rf_state.init_objective = (
                            rf_state.continuous_provider()
                        )
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
                    rf_state.init_objective = current_state.objective
                    if current_state.enabled:
                        rf_state.suppress.update(
                            {
                                "active": True,
                                "objective": current_state.objective,
                                "generation": current_state.generation,
                            }
                        )
                else:
                    rf_state.init_continuous, rf_state.init_objective = (
                        rf_state.continuous_provider()
                    )
                rf_state.cfg.continuous_objective = rf_state.init_objective
                log.error("daemon Manager handoff failed; objective not dispatched: %s", exc)
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
        # ``full_paper`` vertical is positively resolved), so constructing this
        # before divide would incorrectly leave a brand-new paper campaign in
        # bounded mode for its whole daemon lifetime.
        sup_cfg = _build_supervisor_config(
            rf_state.cfg,
            runtime_root=rf_state.runtime_root,
            stop_event=self._stop,
            init_continuous=rf_state.init_continuous,
            init_objective=rf_state.init_objective,
            continuous_provider=rf_state.continuous_provider,
            post_mission_hook=self._post_mission_hook,
        )

        # Lazy proxy: resolve ``LifeSupervisor`` through the facade module's
        # OWN namespace at call time (not this module's), so
        # ``monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", ...)``
        # in tests still takes effect even though this method now lives here.
        from .life_worker import LifeSupervisor

        rf_state.sup = LifeSupervisor(
            memory=rf_state.mem,
            runner=rf_state.runner,
            sink=rf_state.sink,
            config=sup_cfg,
            engineer_model=rf_state.cfg.engineer_model,
            reviewer_model=rf_state.cfg.reviewer_model,
            planner_runner=getattr(rf_state.runner, "planner_backend", None)
            or getattr(rf_state.runner, "backend", None),
        )
