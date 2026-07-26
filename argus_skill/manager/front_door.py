"""Manager front-door routing shared by the Ink TUI and Web API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable

from ..core.knobs import resolve_role_model
from ..core.progress_step import REPLY_KINDS
from ..core.runner_errors import is_pre_provider_refusal_error
from ..core.secret_guard import known_secret_values, redact_secrets_text


log = logging.getLogger(__name__)


class ManagerHandoffError(RuntimeError):
    """Manager did not produce a safe Planner/Engineer execution handoff."""


class ManagerHandoffSupersededError(ManagerHandoffError):
    """A newer continuous command superseded an in-flight Manager handoff."""


def objective_update_requires_stage_reset(
    previous_objective: str,
    *updated_objectives: str,
) -> bool:
    """Return whether a continuous-objective update replaces prior work.

    Continuous objectives are commonly extended with operator clarifications,
    authorizations, or constraints.  Those monotonic additions must update the
    standing objective without resetting a certified pipeline back to its first
    stage.  A genuinely different objective still requires the replacement
    reset.  Whitespace-only rewrites are treated as the same objective.

    Callers may provide both the raw operator objective and the Manager-clean
    execution task; an additive relationship in either representation is
    sufficient to preserve the current stage.
    """

    previous = " ".join(str(previous_objective or "").split())
    if not previous:
        return False
    for candidate in updated_objectives:
        current = " ".join(str(candidate or "").split())
        if current == previous or current.startswith(f"{previous} "):
            return False
    return True


def require_manager_execution_task(division: Any) -> str:
    execution_task = str(
        getattr(division, "execution_task", "") or ""
    ).strip()
    if not execution_task:
        raise ManagerHandoffError(
            "Manager did not produce a non-empty execution_task; task was not dispatched"
        )
    return execution_task


def _life_dir_for(mem: Any) -> Path:
    """Resolve the per-project life-dir that holds ``events.jsonl``.

    Works for both ``MemoryBundle`` (``.project.root`` / ``.project_root``)
    and the bare ``LifeMemory`` facade (``.root``) used in tests.
    """
    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root", None)
    if project_root is None:
        raise AttributeError(
            "cannot resolve life-dir: memory has no project_root / project.root / root"
        )
    return Path(project_root)


def mission_is_running(mem: Any) -> bool:
    try:
        return any(
            str(getattr(item, "status", "") or "") == "running"
            for item in mem.backlog.all()
        )
    except Exception:  # noqa: BLE001 - routing must remain available
        return False


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

# Sentinel stored in chat_state when a Manager runner cannot be built (or is
# not applicable, e.g. the memory backend). Lets us cache the "no front-end
# triage" decision so we don't retry the build on every line typed.
_MANAGER_RUNNER_UNAVAILABLE = object()


def _operator_workspace(chat_state: dict[str, Any], session_root: Any) -> Path:
    fallback = (
        Path(session_root).expanduser()
        if session_root
        else Path.cwd()
    )
    sid = str(chat_state.get("session_id") or "").strip()
    global_root = chat_state.get("global_root")
    if not sid or global_root is None:
        return fallback
    from ..core.session import read_session_meta, resolve_session_workdir

    meta = read_session_meta(Path(global_root).expanduser(), sid)
    return resolve_session_workdir(meta, state_dir=fallback)


def _ensure_manager_runner(chat_state: dict[str, Any], mem: Any) -> Any:
    """Lazily build (and cache) a Manager-front-end runner for chat triage.

    The runner is used ONLY to classify free text as chat-vs-task and, when
    chat, to reply in-band BEFORE anything reaches the backlog. It is built
    once per Manager session and cached on ``chat_state["manager_runner"]``.

    Returns the runner, or ``None`` when front-end triage is not available.
    The memory backend is permanently marked unavailable; transient build
    failures are not cached so the next operator turn can recover.
    """
    backend = chat_state.get("backend")
    # The memory backend has no real LLM runner; never triage — every line is
    # a task (preserves existing memory-backend behaviour and its tests).
    if backend == "memory":
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    try:
        # ``manager_session_root`` MUST match the daemon's own
        # ``ns.manager_session_root = str(cfg.life_dir)`` (see
        # ``daemon/life_worker.py:_runner_namespace``) — otherwise this
        # front-door Manager (built once per cockpit session, used for
        # SELF/TEAM routing + ``divide()``) reads/writes
        # ``research/PIPELINE_STATE.json`` and ``research/DOMAINS/*.json``
        # against a DIFFERENT root than the daemon that actually executes
        # the mission. That mismatch silently drops a Manager-authored
        # custom domain (e.g. an operator task that doesn't match any
        # built-in vertical) and logs a spurious
        # ``load_vertical(...): unknown/half-built vertical`` warning the
        # next time the daemon resolves the vertical from ITS (correct,
        # session-scoped) root. ``mem.project_root`` is the per-project
        # session dir; ``mem.root`` (used below for ``life_dir``, a
        # differently-scoped, currently-unread-by-this-path field) is the
        # GLOBAL ``~/.argus-skill`` root — do not conflate the two.
        session_root = getattr(mem, "project_root", None)
        operator_workspace = _operator_workspace(chat_state, session_root)
        workspace_key = str(operator_workspace)
        cached = chat_state.get("manager_runner")
        if (
            cached is not None
            and chat_state.get("manager_runner_workdir") == workspace_key
        ):
            return None if cached is _MANAGER_RUNNER_UNAVAILABLE else cached
        if cached is not None:
            chat_state.pop("manager_runner", None)
            chat_state.pop("manager_runner_workdir", None)
        ns = argparse.Namespace(
            backend=backend or "codex",
            engineer_model=resolve_role_model(
                "engineer",
                role_env="ARGUS_SKILL_ENGINEER_MODEL",
            ),
            reviewer_model=resolve_role_model(
                "reviewer",
                role_env="ARGUS_SKILL_REVIEWER_MODEL",
            ),
            engineer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh"
            ),
            reviewer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
            ),
            plan_mode="auto",
            plan_model=None,
            max_rounds=500,
            # The Manager uses the same persisted workdir as Planner, Engineer,
            # and Reviewer. Session state remains rooted at session_root.
            workdir=workspace_key,
            operator_workspace=str(operator_workspace),
            manager_session_root=str(session_root) if session_root else None,
            project_state_dir=str(session_root) if session_root else None,
            global_root=str(mem.global_root),
            life_dir=getattr(mem, "root", None),
            stop_event=None,
        )
        from ..apps._runtime import build_life_runner

        runner = build_life_runner(ns)
        manager_backend = getattr(runner, "_backend", None)
        set_acp_scope = getattr(manager_backend, "set_acp_scope", None)
        if callable(set_acp_scope):
            set_acp_scope(f"manager:{chat_state.get('session_id') or workspace_key}")
    except Exception:  # noqa: BLE001 — retry on the next operator turn
        return None

    chat_state["manager_runner"] = runner
    chat_state["manager_runner_workdir"] = str(operator_workspace)
    return runner


def _derive_session_name(text: str, *, limit: int = 48) -> str:
    """Create a safe deterministic fallback label from the first real task.

    The normal front-door path asks Manager to distill a concise semantic title.
    This fallback guarantees a usable label when that cosmetic model output is
    unavailable: take the first non-empty line, normalize it, and truncate.
    """
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip().strip("`\"'“”‘’")
        line = line.rstrip("。.!！?？;；:：")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _maybe_name_session(
    chat_state: dict[str, Any],
    task_text: str,
    *,
    suggested_name: str = "",
) -> None:
    """Name the current session after its first real task (once, fail-soft).

    A resumed session keeps its original name (``session_named`` is already
    True). Only the first task in a freshly-minted, still-unnamed session sets
    the display_name shown in the resume picker. Prefer Manager's concise title;
    use the deterministic first-line label only when no title was produced.
    """
    if chat_state.get("session_named"):
        return
    sid = chat_state.get("session_id")
    gr = chat_state.get("global_root")
    if not sid or gr is None:
        return
    try:
        from ..core.session import read_session_meta, touch_session

        persisted = read_session_meta(gr, sid)
        if persisted is not None and persisted.display_name.strip():
            chat_state["session_named"] = True
            return
        name = (
            _derive_session_name(suggested_name, limit=32)
            or _derive_session_name(task_text)
        )
        if not name:
            return
        touch_session(gr, sid, display_name=name)
        chat_state["session_named"] = True
    except Exception:  # noqa: BLE001 — naming is cosmetic, never block the task
        pass


def _emit_manager_event(mem: Any, event: dict[str, Any]) -> None:
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=_life_dir_for(mem)).append(event)
    except Exception:  # noqa: BLE001
        pass


def _manager_current_stage(manager: Any) -> str:
    resolver = getattr(manager, "current_stage", None)
    if not callable(resolver):
        return ""
    try:
        return str(resolver() or "").strip()
    except Exception:  # noqa: BLE001 - event enrichment must never break handoff
        return ""


def _accepts_keyword(fn: Any, name: str) -> bool:
    try:
        parameters = signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == name or parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _record_goal_contract(mem: Any, body: str, division: Any) -> None:
    """Write the Project's goal contract once, at the moment Manager commits.

    Only on first commit: a later commit revising an existing contract has to go
    through ``revise_contract``, which is where the precise/semantic rule lives.
    Overwriting here would let any re-triage silently reset a constraint the
    operator had confirmed.

    Additive today — nothing gates completion on the contract yet (operator
    decision §9.6 exempts existing projects), so a failure to record one must
    never take down the handoff that was otherwise fine.
    """
    try:
        from ..core.project_contract import (
            CLAUSE_PRECISE,
            CLAUSE_SEMANTIC,
            load_contract,
            make_clause,
            new_contract,
            save_contract,
        )

        state_dir = _life_dir_for(mem)
        if load_contract(state_dir) is not None:
            return
        clauses = []
        for text in getattr(division, "precise_constraints", ()) or ():
            clauses.append(make_clause(CLAUSE_PRECISE, text))
        target = str(getattr(division, "research_target_level", "") or "").strip()
        if target:
            clauses.append(
                make_clause(CLAUSE_SEMANTIC, f"research target level: {target}")
            )
        venue = str(getattr(division, "target_venue", "") or "").strip()
        if venue:
            clauses.append(make_clause(CLAUSE_SEMANTIC, f"target venue: {venue}"))
        save_contract(
            state_dir,
            contract=new_contract(
                objective=body,
                clauses=clauses,
                ambiguities=getattr(division, "ambiguities", ()) or (),
            ),
        )
    except Exception:  # noqa: BLE001 — see docstring; recording is additive
        log.debug("could not record goal contract", exc_info=True)


@dataclass
class PreparedManagerHandoff:
    mem: Any
    body: str
    manager: Any
    decision: Any
    intent_id: str
    root_task_id: str | None

    @property
    def execution_task(self) -> str:
        return require_manager_execution_task(self.decision)

    def commit(
        self,
        *,
        acquire_lock: bool = True,
        force_stage_reset: bool = False,
    ) -> Any:
        kwargs = {} if acquire_lock else {"_lock_held": True}
        division = self.manager.commit_vertical_decision(
            self.body,
            self.decision,
            ask_on_new_domain=False,
            force_stage_reset=force_stage_reset,
            **kwargs,
        )
        require_manager_execution_task(division)
        _record_goal_contract(self.mem, self.body, division)
        return division

    def completed(
        self,
        division: Any,
        *,
        continuous_generation: int | None = None,
    ) -> None:
        event = {
            "type": "life.manager.intent.completed",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "objective": self.body,
            "execution_task": self.execution_task,
            "vertical": getattr(division, "vertical", ""),
            "domain": getattr(division, "domain", ""),
            "workflow_mode": getattr(division, "workflow_mode", "staged"),
            "kind": getattr(division, "kind", ""),
            "stages": list(getattr(division, "stages", []) or []),
            "reason": getattr(division, "headline", lambda: "")(),
            "text": (
                f"manager interpreted user task as "
                f"{getattr(division, 'vertical', '')}"
            ),
        }
        if continuous_generation is not None:
            event["continuous_generation"] = continuous_generation
        current_stage = _manager_current_stage(self.manager)
        if current_stage:
            event["current_stage"] = current_stage
        _emit_manager_event(self.mem, event)

    def failed(self, exc: Exception) -> None:
        _emit_manager_event(self.mem, {
            "type": "life.manager.intent.failed",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "objective": self.body,
            "error": f"{type(exc).__name__}: {exc}",
            "text": "manager intent interpretation failed",
        })

    def superseded(self) -> None:
        _emit_manager_event(self.mem, {
            "type": "life.manager.intent.superseded",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "text": "newer continuous command superseded Manager handoff",
        })


def prepare_manager_execution_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
) -> PreparedManagerHandoff:
    intent_id = f"intent-{time.time_ns()}"
    _emit_manager_event(mem, {
        "type": "life.manager.intent.started",
        "agent_layer": "manager",
        "intent_id": intent_id,
        "item_id": root_task_id,
        "source": "user",
        "objective": body,
        "text": "manager interpreting user task",
    })
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        manager = getattr(runner, "manager", None) if runner is not None else None
        if manager is None:
            from ..manager import Manager

            manager = Manager(
                project_root=getattr(mem, "project_root", None) or Path.cwd(),
                runner=None,
            )

        if root_task_id is None or not _accepts_keyword(
            manager.decide_vertical,
            "root_task_id",
        ):
            decision = manager.decide_vertical(body)
        else:
            decision = manager.decide_vertical(body, root_task_id=root_task_id)
        require_manager_execution_task(decision)
        return PreparedManagerHandoff(
            mem=mem,
            body=body,
            manager=manager,
            decision=decision,
            intent_id=intent_id,
            root_task_id=root_task_id,
        )
    except Exception as exc:
        prepared = PreparedManagerHandoff(
            mem=mem,
            body=body,
            manager=None,
            decision=None,
            intent_id=intent_id,
            root_task_id=root_task_id,
        )
        prepared.failed(exc)
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager handoff failed: {exc}") from exc


def _manager_divide_user_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
) -> Any:
    """Run Manager division for an operator-submitted task before enqueue.

    This is intentionally a USER-ENTRY gate. Planner-generated backlog items are
    already the Planner's decomposition and must not be routed back through
    Manager again.

    The caller surfaces progress through the Web/TUI event stream while this
    blocking Manager decision runs.
    """
    try:
        prepared = prepare_manager_execution_task(
            mem,
            body,
            chat_state,
            root_task_id=root_task_id,
            ensure_runner=ensure_runner,
        )
    except ManagerHandoffError:
        return None
    try:
        division = prepared.commit()
        prepared.completed(division)
        return division
    except Exception as exc:  # noqa: BLE001
        prepared.failed(exc)
        return None


def manager_execution_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> str:
    """Return Manager's role-clean Planner/Engineer handoff or fail closed."""
    division = _manager_divide_user_task(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
    )
    return require_manager_execution_task(division)


def manager_bounded_handoff(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    persist: Callable[[str, Any], Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    prepare_persist: Callable[[str], None] | None = None,
) -> Any:
    """Commit Manager state and durable task enqueue under one pipeline lock.

    A bounded operator task submitted while a continuous campaign is active is
    supplemental work inside that campaign. The Manager may rewrite the task
    into a role-clean execution handoff, but it must not replace the standing
    campaign's vertical, stage, target level, or workflow mode.
    """
    prepared = prepare_manager_execution_task(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
        ensure_runner=ensure_runner,
    )
    lock_factory = getattr(prepared.manager, "pipeline_lock", None)
    pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
    try:
        if prepare_persist is not None:
            prepare_persist(prepared.execution_task)
        with pipeline_lock:
            division = _bounded_handoff_division(
                prepared,
                chat_state=chat_state,
            )
            if division is None:
                division = prepared.commit(acquire_lock=False)
            result = persist(prepared.execution_task, division)
            prepared.completed(division)
            return result
    except Exception as exc:
        prepared.failed(exc)
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager bounded handoff failed: {exc}") from exc


def _bounded_handoff_division(
    prepared: PreparedManagerHandoff,
    *,
    chat_state: dict[str, Any],
) -> Any | None:
    """Return a non-mutating Division for supplemental continuous work."""
    from ..daemon.state import read_continuous_state

    life_dir = _life_dir_for(prepared.mem)
    continuous = read_continuous_state(life_dir)
    if not continuous.enabled or not continuous.objective.strip():
        return None

    from ..skills.vertical_select import resolve_vertical, resolve_workflow_mode
    from ._core import Division

    project_root = Path(
        getattr(prepared.manager, "project_root", None)
        or _operator_workspace(chat_state, life_dir)
    )
    vertical = resolve_vertical(project_root)
    return Division(
        task=prepared.body,
        vertical=vertical,
        kind=prepared.manager._kind_for(vertical),
        stages=list(prepared.manager.plan_stages(vertical)),
        workflow_mode=resolve_workflow_mode(project_root),
        execution_task=prepared.execution_task,
    )


def manager_continuous_handoff(
    mem: Any,
    requested_objective: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Atomically enable a Manager-authored continuous objective."""
    from ..daemon.state import (
        compare_and_swap_continuous_config,
        read_continuous_state,
    )

    life_dir = _life_dir_for(mem)
    expected = read_continuous_state(life_dir)
    body = requested_objective.strip() or expected.objective.strip()
    if not body:
        raise ValueError("continuous mode requires a non-empty objective")
    prepared = prepare_manager_execution_task(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
        ensure_runner=ensure_runner,
    )
    committed: dict[str, Any] = {}
    replacement_intent = bool(
        expected.objective.strip()
        and requested_objective.strip()
        and objective_update_requires_stage_reset(
            expected.objective,
            body,
            prepared.execution_task,
        )
    )

    def _commit() -> None:
        if callable(cancelled) and cancelled():
            raise ManagerHandoffError("Manager request cancelled before commit")
        committed["division"] = prepared.commit(
            acquire_lock=False,
            force_stage_reset=replacement_intent,
        )
        if replacement_intent:
            backlog = getattr(mem, "backlog", None)
            supersede = getattr(
                backlog,
                "supersede_pending_for_replacement",
                None,
            )
            if callable(supersede):
                committed["superseded_ids"] = supersede(
                    reason="operator replaced the standing Manager objective",
                    replacement_id=prepared.intent_id,
                )

    from ._session_ops import (
        clear_manager_pipeline_yield,
        request_manager_pipeline_yield,
    )

    yield_token = request_manager_pipeline_yield(life_dir)
    try:
        lock_factory = getattr(prepared.manager, "pipeline_lock", None)
        pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
        with pipeline_lock:
            swapped = compare_and_swap_continuous_config(
                life_dir,
                expected=expected,
                enabled=True,
                objective=prepared.execution_task,
                before_write=_commit,
            )
    except Exception as exc:
        prepared.failed(exc)
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager handoff commit failed: {exc}") from exc
    finally:
        clear_manager_pipeline_yield(life_dir, yield_token)
    if not swapped:
        prepared.superseded()
        current = read_continuous_state(life_dir)
        if current.generation == expected.generation:
            raise ManagerHandoffError(
                "Manager execution handoff could not be persisted"
            )
        raise ManagerHandoffSupersededError(
            "newer continuous command superseded Manager handoff"
        )
    prepared.completed(
        committed["division"],
        continuous_generation=expected.generation + 1,
    )
    for item_id in committed.get("superseded_ids", ()):
        _emit_manager_event(mem, {
            "type": "life.plan.node.superseded",
            "item_id": item_id,
            "superseded_by_plan_id": prepared.intent_id,
            "reason": "operator replaced the standing Manager objective",
            "source": "manager_intent_replacement",
        })
    return prepared.execution_task


_DO_NOT_RUN_MARKERS: tuple[str, ...] = (
    # Chinese (simplified + a few traditional variants)
    "不要运行", "不要執行", "不要执行", "不要启动", "不要啟動",
    "别运行", "別運行", "别启动", "別啟動", "不要跑", "不要派发", "不要分派",
    "不要运行任务", "只做状态检查", "只检查状态", "只看状态", "只查状态",
    "状态检查", "狀態檢查", "请回复状态正常", "請回復狀態正常",
    # English
    "do not run", "don't run", "dont run",
    "do not execute", "don't execute", "dont execute",
    "do not start", "don't start", "dont start",
    "do not launch", "don't launch", "do not dispatch", "do not spawn",
    "status check only", "status-only", "status only",
    "just check status", "only check status",
)


def looks_like_do_not_run_request(text: str) -> bool:
    """True iff ``text`` explicitly forbids running / asks for status only.

    Used ONLY to make the triage-failure fallback safe (see
    :func:`manager_triage`): when the Manager's classify call ERRORS, the front
    door normally biases to "task" ("never drop work to a bad classify"), but if
    the operator explicitly said "do not run / status only" then creating a real
    mission on a *failed* classify is the wrong default — that is exactly how a
    Chinese "请只做状态检查，不要运行任务" message got dispatched to the team on
    2026-07-11 (the Manager's classify call had been blocked by the cost gate, so
    triage raised and the message was treated as work). This never overrides a
    SUCCESSFUL classify decision, so it cannot silently drop genuine work.
    """
    if not text:
        return False
    raw = str(text)
    low = raw.lower()
    for marker in _DO_NOT_RUN_MARKERS:
        if marker in raw or marker.lower() in low:
            return True
    return False


_DO_NOT_RUN_SAFE_REPLY = (
    "[not dispatched] The Manager could not classify this request and your "
    "message asks not to run anything (status-only / do-not-run), so no task was "
    "queued. Use /status for pipeline state or /doctor to diagnose; rephrase "
    "without the do-not-run constraint if you actually want to queue work."
)


def _fallback_request_excerpt(body: str) -> str:
    compact = " ".join(str(body or "").split())
    return compact if len(compact) <= 160 else compact[:157] + "..."


def _pre_provider_refusal_reply(exc: Exception, body: str) -> str:
    return (
        "[not dispatched] The Manager could not classify this message because "
        f"the provider call was refused before start: {exc}. "
        f"Request: {_fallback_request_excerpt(body)}"
    )


def manager_triage(mem: Any, body: str, chat_state: dict[str, Any],
                   *, on_phase: Any = None, on_fragment: Any = None,
                   route: str | None = None,
                   root_task_id: str | None = None,
                   ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
                   ) -> str | None:
    """Front-door route: one-Codex SELF work returns a reply; TEAM work returns
    ``None`` so the caller queues the Argus Planner/Engineer/Reviewer pipeline.

    ``on_phase(label, *, role=...)`` — optional callback invoked at the REAL
    phase transitions (classify → reply), so a live status line reflects what
    the Manager is actually doing rather than a timed cosmetic rotation.
    ``role`` is a best-effort extra (falls back to the plain one-arg call for
    any callback that does not accept it) naming which of the four roles
    drove this update, so the caller can retint a live spinner to match.

    ``on_fragment(kind, payload)`` — optional streaming callback for a live
    front-end (the web/TUI SSE bridge). Fires ``("delta", {"text", "message_id"})``
    for each assistant reply block the instant it arrives, and ``("phase",
    {"role", "label"})`` at each phase transition.
    """
    if route is None and mission_is_running(mem):
        route = "simple"
    runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
    if runner is None or not hasattr(runner, "chat_reply_if_conversational"):
        return None
    captured: list[str] = []
    empty_reply = (
        "[Manager reply unavailable] The SELF turn completed without an assistant "
        "message. No task was dispatched and the current mission was not changed. "
        f"Request: {_fallback_request_excerpt(body)}"
    )

    def _redact_live_text(text: Any) -> str:
        return redact_secrets_text(str(text or ""), known_values=known_secret_values())

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
        if kind in {"delta", "phase"}:
            payload = dict(payload)
            for key in ("text", "label", "detail"):
                if key in payload:
                    payload[key] = _redact_live_text(payload[key])
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — a UI callback must never break triage
            pass

    def _progress_label(event: dict[str, Any]) -> tuple[str, str] | None:
        try:
            from ..apps.cli._follow import _clean_follow_text
            txt = _redact_live_text(
                event.get("text")
                or event.get("title")
                or event.get("reason")
                or event.get("kind")
                or ""
            ).strip()
            if not txt:
                return None
            role = str(event.get("agent_layer") or "manager").strip() or "manager"
            title = {
                "manager": "Manager",
                "planner": "Planner",
                "engineer": "Engineer",
                "reviewer": "Reviewer",
            }.get(role, role.title())
            return role, title + " · " + _clean_follow_text(txt, limit=64)
        except Exception:  # noqa: BLE001
            return None

    def _emit_phase(role: str, label: str, *, kind: str = "", detail: str = "") -> None:
        # Relay every real runner phase to both callback styles so SSE sees
        # classify/direct-reply transitions instead of a generic spinner.
        safe_label = _redact_live_text(label)
        safe_detail = _redact_live_text(detail)
        if callable(on_phase):
            for kwargs in (
                {"role": role, "kind": kind, "detail": safe_detail},
                {"role": role},
                {},
            ):
                try:
                    on_phase(safe_label, **kwargs)
                    break
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001 — a UI callback must never break triage
                    break
        payload: dict[str, Any] = {"role": role, "label": safe_label}
        if kind:
            payload["kind"] = kind
        if safe_detail:
            payload["detail"] = safe_detail
        _fragment("phase", payload)

    def _runner_phase(
        label: str,
        *,
        role: str = "manager",
        kind: str = "",
        detail: str = "",
    ) -> None:
        _emit_phase(
            str(role or "manager"),
            str(label or ""),
            kind=str(kind or ""),
            detail=str(detail or ""),
        )

    class _Capture:
        def __init__(self, *, progress_phases: bool) -> None:
            self._progress_phases = progress_phases

        def handle_event(self, event: dict[str, Any]) -> None:
            try:
                etype = str(event.get("type") or "")
                # A live assistant reply block → stream it as a delta fragment
                # (grows the reply in the front-end) rather than treating it as a
                # phase label. Keep capturing the authoritative reply below.
                if etype == "engineer.progress" and str(event.get("kind") or "") in REPLY_KINDS:
                    blk = _redact_live_text(event.get("text")).strip()
                    if blk:
                        _fragment("delta", {
                            "text": blk,
                            "message_id": str(event.get("message_id") or ""),
                        })
                    return
                if etype in {"loop.start", "engineer.progress"}:
                    # The current runner reports these same events through its
                    # phase_cb wrapper, already normalized as Manager activity.
                    # Only legacy runners (which reject phase_cb and hit the
                    # fallback below) need the capture sink to synthesize them.
                    if self._progress_phases:
                        parsed = _progress_label(event)
                        if parsed:
                            _emit_phase(*parsed)
                    return
                if etype != "round.main.completed":
                    return
                text = _redact_live_text(
                    _extract_chat_reply_text(str(event.get("last_message") or ""))
                )
                if text:
                    captured.append(text)
            except Exception:  # noqa: BLE001
                pass

    try:
        triage_kwargs: dict[str, Any] = {
            "objective": body,
            "sink": _Capture(progress_phases=False),
            "seed_thread_id": chat_state.get("last_thread_id"),
            "phase_cb": _runner_phase,
            "route": route,
        }
        if root_task_id is not None and _accepts_keyword(
            runner.chat_reply_if_conversational,
            "root_task_id",
        ):
            triage_kwargs["root_task_id"] = root_task_id
        if runner.chat_reply_if_conversational(**triage_kwargs):
            chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
            return captured[0] if captured else empty_reply
    except TypeError:
        # Older runner without phase_cb / route support — retry without them
        # (fail-soft; the older runner will classify route internally).
        try:
            if runner.chat_reply_if_conversational(
                objective=body, sink=_Capture(progress_phases=True),
                seed_thread_id=chat_state.get("last_thread_id"),
            ):
                chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
                return captured[0] if captured else empty_reply
        except Exception as exc:  # noqa: BLE001 — triage failure
            if looks_like_do_not_run_request(body):
                return _DO_NOT_RUN_SAFE_REPLY
            if is_pre_provider_refusal_error(exc):
                return _pre_provider_refusal_reply(exc, body)
            return None
    except Exception as exc:  # noqa: BLE001 — triage failure: bias to task ("never drop
        # work to a bad classify") UNLESS the operator explicitly forbade running
        # (status-only / do-not-run). Dispatching a real mission on a classify we
        # could not even complete is how a status request reached the Engineer.
        if looks_like_do_not_run_request(body):
            return _DO_NOT_RUN_SAFE_REPLY
        if is_pre_provider_refusal_error(exc):
            return _pre_provider_refusal_reply(exc, body)
        return None
    return None

def _extract_chat_reply_text(msg: str) -> str:
    """Pull the human reply out of a chat result (plain text, or JSON-wrapped)."""
    msg = (msg or "").strip()
    if "📢" in msg:
        msg = msg.rsplit("📢", 1)[1].strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
            # Pending-question resolution consumes this structured Manager
            # decision downstream. Do not collapse it to its operator-facing
            # ``reply`` field before that parser sees it.
            if (
                isinstance(data.get("is_answer"), bool)
                and isinstance(data.get("resolved"), bool)
            ):
                return msg
            for key in ("reply", "message", "text", "answer", "response"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:  # noqa: BLE001
            pass
    return msg

__all__ = [
    "_DO_NOT_RUN_SAFE_REPLY",
    "_accepts_keyword",
    "_MANAGER_RUNNER_UNAVAILABLE",
    "ManagerHandoffError",
    "ManagerHandoffSupersededError",
    "PreparedManagerHandoff",
    "_derive_session_name",
    "_emit_manager_event",
    "_ensure_manager_runner",
    "_extract_chat_reply_text",
    "_life_dir_for",
    "_manager_divide_user_task",
    "_maybe_name_session",
    "looks_like_do_not_run_request",
    "manager_execution_task",
    "manager_bounded_handoff",
    "manager_continuous_handoff",
    "manager_triage",
    "mission_is_running",
    "prepare_manager_execution_task",
    "require_manager_execution_task",
]
