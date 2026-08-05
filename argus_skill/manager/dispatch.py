"""Manager-owned task lifetime and durable dispatch."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

from ..apps._life_actions import DEFAULT_LIFE_CONFIG
from . import front_door

DEFAULT_MANAGER_CONFIG = DEFAULT_LIFE_CONFIG


def _resolve_manager_workdir(mem: Any) -> Path:
    from ..core.session import read_session_meta, resolve_session_workdir

    life_dir = Path(front_door._life_dir_for(mem))
    global_root = getattr(mem, "global_root", None)
    root = Path(global_root) if global_root is not None else life_dir.parent.parent
    meta = read_session_meta(root, life_dir.name)
    if meta is not None and (
        str(getattr(meta, "workdir", "") or "").strip()
        or str(getattr(meta, "cwd", "") or "").strip()
    ):
        return resolve_session_workdir(meta, state_dir=life_dir)
    configured = getattr(mem, "project_worktree", None)
    if configured is not None:
        return Path(configured).expanduser().resolve()
    return resolve_session_workdir(meta, state_dir=life_dir)


def _stable_topological_nodes(tasks: tuple[Any, ...]) -> list[Any]:
    ordered: list[Any] = []
    done: set[str] = set()
    remaining = list(tasks)
    while remaining:
        ready = [node for node in remaining if set(node.deps) <= done]
        if not ready:
            raise front_door.ManagerHandoffError("bounded Planner returned a cyclic DAG")
        for node in ready:
            ordered.append(node)
            done.add(node.key)
            remaining.remove(node)
    return ordered


def _plan_bounded_execution(
    mem: Any,
    execution_body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> Any:
    runner = front_door._ensure_manager_runner(chat_state, mem)
    backend = getattr(runner, "planner_backend", None) if runner is not None else None
    if backend is None:
        raise front_door.ManagerHandoffError(
            "bounded Planner backend unavailable; refusing an atomic fallback "
            "that cannot preserve review and stage-transition semantics"
        )
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import (
        resolve_knob,
        resolve_role_backend,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )
    from ..planner.bounded_dag import plan_bounded_dag

    workdir = _resolve_manager_workdir(mem)
    configured_model = resolve_knob(
        "ARGUS_SKILL_BOUNDED_DAG_MODEL",
        "auto",
    ).value.strip()
    if configured_model.lower() in {"", "auto", "inherit", "default"}:
        planner_backend = normalize_runner_backend(resolve_role_backend("planner"))
        model = (
            "gpt-5.4-mini"
            if planner_backend in {"codex", "copilot", "pi"}
            else resolve_role_model("planner", role_env="ARGUS_SKILL_PLAN_MODEL")
        )
    else:
        model = configured_model
    usage_scope = getattr(runner, "task_usage_context", None)
    scope = usage_scope(root_task_id) if callable(usage_scope) and root_task_id else nullcontext()
    with scope:
        plan = plan_bounded_dag(
            backend,
            execution_body,
            workdir=workdir,
            model=model,
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_BOUNDED_DAG_REASONING_EFFORT",
                default="low",
            ),
        )
    if plan.error or not plan.tasks:
        raise front_door.ManagerHandoffError(
            f"bounded Planner could not produce an executable DAG: {plan.error or 'empty plan'}"
        )
    return plan


def resume_done_lifecycle_for_team_dispatch(mem: Any) -> bool:
    """Resume a completed project lifecycle when new TEAM work arrives.

    Returns True if the lifecycle was actually resumed (state was ``done``).
    Returns False for already-active states or missing lifecycle data.
    Raises RuntimeError for quarantined/archived (explicit resume required).

    Concurrency note
    ----------------
    The read (``load_persisted``) and the write (``resume_atomically_if_done``)
    are NOT fully lock-atomic end-to-end: ``infer_observable_status`` and
    ``apply_persisted_to_status`` run between them.  If two concurrent callers
    simultaneously observe ``done``, both compute a resumed ``new_status``, and
    then ``resume_atomically_if_done`` serialises the actual write — only the
    first caller's write lands; the second caller's no-ops (returns False,
    treated as True here since the project IS resumed).  In the worst case both
    writes land back-to-back, which is idempotent.  This is a residual low-risk
    TOCTOU; ``append_event`` atomic persistence is preserved and correct.
    """
    from ..core.session import read_session_meta, resolve_session_workdir
    from ..life.project_lifecycle import (
        infer_observable_status,
    )
    from ..life.project_lifecycle import (
        resume as lifecycle_resume,
    )
    from ..life.project_lifecycle_io import (
        apply_persisted_to_status,
        load_persisted,
        resume_atomically_if_done,
    )

    life_dir = Path(front_door._life_dir_for(mem))
    persisted = load_persisted(life_dir)
    state = str(persisted.get("state") or "")
    if not state:
        return False
    if state != "done":
        if state in {"quarantined", "archived"}:
            raise RuntimeError(
                f"project lifecycle is {state}; explicit resume is required"
            )
        return False
    # Prefer mem.global_root (MemoryBundle attribute) for a stable path; fall
    # back to path arithmetic only when the object lacks the attribute.
    global_root = getattr(mem, "global_root", None)
    root = Path(global_root) if global_root is not None else life_dir.parent.parent
    meta = read_session_meta(root, life_dir.name)
    observable_root = resolve_session_workdir(meta, state_dir=life_dir)
    status = infer_observable_status(observable_root, project_id=life_dir.name)
    status = apply_persisted_to_status(status, persisted)
    new_status, event = lifecycle_resume(
        status,
        reason="manager_team_dispatch",
    )
    # Atomic check-then-write: only commits if persisted state is still "done".
    # Returns False if a concurrent caller already resumed — treat as success.
    resume_atomically_if_done(life_dir, new_status=new_status, event=event)
    return True


def _daemon_status(life_dir: Any) -> tuple[bool, int | None]:
    try:
        from ..daemon.life_worker import read_daemon_status

        status = read_daemon_status(life_dir)
        return bool(status.alive), status.pid if status.alive else None
    except Exception:  # noqa: BLE001 - dispatch still succeeds without status
        return False, None


def enqueue_mission(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    iterate: bool = True,
    max_cycles: int = 6,
    root_task_id: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    prepared_handoff: front_door.PreparedManagerHandoff | None = None,
) -> tuple[Any | None, bool, int | None]:
    """Persist one Manager-authored mission and report executor availability."""
    if chat_state.get("blocked_item_id"):
        prior = str(chat_state.get("last_objective") or body)
        blocked_id = chat_state.pop("blocked_item_id", None)
        chat_state.pop("blocked_question", None)
        try:
            from ..apps._inbox import queue_inbox_message

            queue_inbox_message(
                front_door._life_dir_for(mem),
                body,
                source="manager.answer",
            )
        except Exception:  # noqa: BLE001 - the durable mission remains authoritative
            pass
        if blocked_id:
            try:
                mem.backlog.update(blocked_id, pending_question="")
            except Exception:  # noqa: BLE001 - do not drop the operator reply
                pass
        body = f"{prior}\n\nOperator reply: {body}"

    life_dir = front_door._life_dir_for(mem)
    if chat_state.get("config", {}).get("continuous", False):
        pending_auto_promote = bool(
            chat_state.pop("_continuous_pending_manager_handoff", False)
        )
        try:
            execution_body = front_door.manager_continuous_handoff(
                mem,
                body,
                chat_state,
                root_task_id=root_task_id,
                cancelled=cancelled,
                prepared_handoff=prepared_handoff,
            )
        except Exception:
            if pending_auto_promote:
                chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
                    "continuous"
                ] = False
                chat_state["continuous_objective"] = ""
            raise
        chat_state["last_objective"] = execution_body
        chat_state["continuous_objective"] = execution_body
        front_door._maybe_name_session(chat_state, execution_body)
        alive, pid = _daemon_status(life_dir)
        return None, alive, pid

    planned: dict[str, Any] = {}

    def _hydrate_context_refs(nodes: list[Any]) -> dict[str, list[dict[str, Any]]]:
        from ..planner.planner import hydrate_task_context_refs

        workdir = _resolve_manager_workdir(mem)
        hydrated_refs: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            try:
                hydrated_refs[node.key] = hydrate_task_context_refs(
                    list(getattr(node, "context_refs", ()) or ()),
                    workdir,
                )
            except ValueError as exc:
                raise front_door.ManagerHandoffError(
                    f"bounded Planner returned an invalid context reference: {exc}"
                ) from exc
        return hydrated_refs

    def _prepare_persist(execution_body: str) -> None:
        if callable(cancelled) and cancelled():
            raise front_door.ManagerHandoffError(
                "Manager request cancelled before bounded DAG planning"
            )
        plan = _plan_bounded_execution(
            mem,
            execution_body,
            chat_state,
            root_task_id=root_task_id,
        )
        nodes = _stable_topological_nodes(tuple(getattr(plan, "tasks", ()) or ()))
        if not nodes:
            raise front_door.ManagerHandoffError("bounded Planner produced no tasks")
        for node in nodes:
            stage_closing = bool(getattr(node, "stage_closing", False))
            require_review = bool(
                getattr(node, "require_independent_review", False)
            )
            skip_stage_transition = bool(
                getattr(node, "skip_stage_transition", False)
            )
            if skip_stage_transition and (stage_closing or not require_review):
                raise front_door.ManagerHandoffError(
                    "bounded Planner returned an invalid review-only stage "
                    "transition contract"
                )
        planned["plan"] = plan
        planned["nodes"] = nodes
        planned["hydrated_refs"] = _hydrate_context_refs(nodes)

    def _validate_persist(_execution_body: str) -> None:
        nodes = list(planned.get("nodes") or ())
        current_refs = _hydrate_context_refs(nodes)
        if current_refs != dict(planned.get("hydrated_refs") or {}):
            raise front_door.ManagerHandoffError(
                "bounded Planner context references changed before Manager commit"
            )
        planned["hydrated_refs"] = current_refs

    def _persist(execution_body: str, _division: Any) -> Any:
        if callable(cancelled) and cancelled():
            raise front_door.ManagerHandoffError(
                "Manager request cancelled before backlog commit"
            )
        pending = mem.backlog.pending()
        head_priority = min((item.priority for item in pending), default=100)
        plan = planned.get("plan")
        nodes = list(planned.get("nodes") or ())
        hydrated_refs = dict(planned.get("hydrated_refs") or {})
        from ..life.memory import BacklogItem

        plan_id = f"bounded-{uuid.uuid4().hex[:12]}"
        ids = {
            node.key: (
                str(root_task_id)
                if index == 0 and root_task_id
                else BacklogItem.new_id()
            )
            for index, node in enumerate(nodes)
        }
        items: list[BacklogItem] = []
        priority = min(head_priority - 1, -1)
        for index, node in enumerate(nodes):
            stage_closing = bool(getattr(node, "stage_closing", False))
            require_review = bool(
                getattr(node, "require_independent_review", False)
            )
            skip_stage_transition = bool(
                getattr(node, "skip_stage_transition", False)
            )
            context_refs = hydrated_refs.get(node.key, [])
            item = BacklogItem.new(
                item_id=ids[node.key],
                title=node.title,
                objective=node.objective,
                priority=priority + index,
                tags=[
                    "manager",
                    "planner",
                    "bounded_dag_node",
                    "scope:bounded",
                    *(
                        ["stage_closing"]
                        if stage_closing
                        else []
                    ),
                    *(
                        ["review:required"]
                        if stage_closing or require_review
                        else []
                    ),
                    *(
                        ["stage_transition:skip"]
                        if skip_stage_transition
                        else []
                    ),
                ],
                iterate=False,
                iteration_max_cycles=1,
                deps=[ids[dep] for dep in node.deps],
                plan_id=plan_id,
                plan_version=1,
                node_key=node.key,
                context_refs=context_refs,
                acceptance_check=str(getattr(node, "acceptance_check", "") or ""),
                non_goals=list(getattr(node, "non_goals", ()) or ()),
            )
            item.original_objective = execution_body
            items.append(item)
        mem.backlog.add_many(items)
        item = items[0]
        try:
            from ..core.planner_verdict import (
                PlannerVerdictStatus,
                build_planner_verdict_event,
            )
            from ..life.event_log import JsonlEventSink

            sink = JsonlEventSink(None, life_dir=Path(life_dir))
            reason = str(getattr(plan, "reason", "") or "bounded DAG")
            sink.append(build_planner_verdict_event(
                status=PlannerVerdictStatus.PLANNED,
                reason=reason,
                project_id=Path(life_dir).name,
                mission_id=plan_id,
                plan_id=plan_id,
                enqueued_tasks=len(items),
                new_tasks=len(items),
                text=f"bounded Planner created {len(items)} DAG node(s)",
            ))
            for node_item in items:
                sink.append({
                    "type": "life.planner.task_added",
                    "item_id": node_item.id,
                    "title": node_item.title,
                    "deps": list(node_item.deps),
                    "plan_id": plan_id,
                    "node_key": node_item.node_key,
                })
        except Exception:  # noqa: BLE001
            pass
        front_door._maybe_name_session(chat_state, execution_body)
        return item

    item = front_door.manager_bounded_handoff(
        mem,
        body,
        chat_state,
        _persist,
        root_task_id=root_task_id,
        prepare_persist=_prepare_persist,
        validate_persist=_validate_persist,
        prepared_handoff=prepared_handoff,
    )
    chat_state["last_objective"] = item.original_objective or item.objective
    alive, pid = _daemon_status(life_dir)
    return item, alive, pid


def maybe_promote_to_continuous(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    workflow_mode: str = "",
) -> bool:
    """Resolve finite lifetime and Manager workflow into a dispatch topology.

    The front door decides whether the requested outcome is a single explicit
    increment, finite, or standing. The Manager independently decides whether
    satisfying the complete outcome requires staged progression. An explicit
    increment always stays bounded; an ordinary finite staged outcome uses the
    durable campaign supervisor so its stage gates can advance.
    """
    del root_task_id
    lifetime = str(
        chat_state.pop("_frontdoor_lifetime", "standing") or "standing"
    ).strip().lower()
    normalized_workflow = str(workflow_mode or "").strip().lower()
    if lifetime == "bounded_increment" or (
        lifetime == "bounded" and normalized_workflow != "staged"
    ):
        chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
            "continuous"
        ] = False
        chat_state["continuous_objective"] = ""
        chat_state.pop("_continuous_pending_manager_handoff", None)
        return False

    from ..core.knobs import resolve_role_backend
    from ..daemon.life_worker import (
        continuous_mode_error,
        read_continuous_state,
        read_daemon_status,
    )

    life_dir = Path(front_door._life_dir_for(mem))
    daemon_status = read_daemon_status(life_dir)
    backend = (
        str(daemon_status.life_backend or "")
        if daemon_status.alive
        else resolve_role_backend("")
    )
    error = continuous_mode_error(backend or resolve_role_backend(""), True, body)
    if error:
        raise front_door.ManagerHandoffError(error)
    persisted = read_continuous_state(life_dir)
    if persisted.enabled and persisted.objective.strip():
        chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
            "continuous"
        ] = True
        chat_state["continuous_objective"] = persisted.objective
        chat_state.pop("_continuous_pending_manager_handoff", None)
        return True

    chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
        "continuous"
    ] = True
    chat_state["_continuous_pending_manager_handoff"] = True
    chat_state["continuous_objective"] = ""
    return True


__all__ = [
    "DEFAULT_MANAGER_CONFIG",
    "enqueue_mission",
    "maybe_promote_to_continuous",
    "resume_done_lifecycle_for_team_dispatch",
]
