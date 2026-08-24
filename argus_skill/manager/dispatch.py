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
        base = resolve_session_workdir(meta, state_dir=life_dir)
    else:
        configured = getattr(mem, "project_worktree", None)
        base = (
            Path(configured).expanduser().resolve()
            if configured is not None
            else resolve_session_workdir(meta, state_dir=life_dir)
        )
    from ..core.campaign_workdir import active_campaign_workdir

    return active_campaign_workdir(life_dir, base) or base


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


def _merge_context_refs(
    *groups: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for raw_ref in group or []:
            if not isinstance(raw_ref, dict):
                continue
            ref = {
                str(key): str(value)
                for key, value in raw_ref.items()
                if str(key).strip() and str(value).strip()
            }
            target = str(ref.get("ref") or "").strip()
            if not target:
                continue
            key = (
                str(ref.get("kind") or "").strip(),
                target,
                str(ref.get("attachment_id") or "").strip(),
                str(ref.get("why") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
    return merged


def _bounded_dag_model() -> str:
    """Model for decomposing a bounded Manager task into backlog DAG nodes.

    Deliberately compact — this is a structural decomposition, not the plan
    itself. See ``core.knobs.resolve_cheap_route_model`` for why the backend,
    not just the knob, decides the fallback.
    """
    from ..core.knobs import resolve_cheap_route_model

    return resolve_cheap_route_model(
        knob="ARGUS_SKILL_BOUNDED_DAG_MODEL",
        catalog_default="gpt-5.4-mini",
        role="planner",
        role_env="ARGUS_SKILL_PLAN_MODEL",
    )


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
    from ..core.knobs import resolve_role_reasoning_effort
    from ..planner.bounded_dag import plan_bounded_dag

    workdir = _resolve_manager_workdir(mem)
    model = _bounded_dag_model()
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
    context_refs: list[dict[str, str]] | None = None,
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
        persisted: dict[str, Any] = {}

        def _persist_operator_priority_item(
            execution_body: str,
            division: Any,
        ) -> Any:
            pending = mem.backlog.pending()
            head_priority = min((item.priority for item in pending), default=100)
            from ..life.memory import BacklogItem
            from ..life.supervisor.backlog_guard import decision_evidence

            contextual_body = (
                "[CURRENT OPERATOR MESSAGE]" in body
                and (
                    "[RECENT CONVERSATION CONTEXT" in body
                    or "[BOUNDED TASK CONTEXT" in body
                )
            )
            title_body = execution_body if contextual_body else body
            compact = " ".join(title_body.split()).replace("`", "")
            title = compact if len(compact) <= 96 else compact[:93] + "..."
            item = BacklogItem.new(
                item_id=root_task_id,
                title=title,
                objective=execution_body,
                priority=min(head_priority - 1, -1),
                tags=[
                    "manager",
                    # Deliberately NOT "planner": that tag is a claim of Planner
                    # authorship, and the mission runtime reads it as exactly
                    # that — ``preplanned = "planner" in tags`` — to skip the
                    # advisory planning pass on the ground that a Planner has
                    # already decomposed the work. This item is a raw operator
                    # message the Manager routed; nothing decomposed it. Tagging
                    # it here suppressed the Planner for the one case
                    # ``_maybe_draft_plan`` documents as needing it ("user-
                    # authored bounded work now follows the full team chain:
                    # Manager → Planner → Engineer → Reviewer"), sending the
                    # objective straight to a single Engineer. The Planner-
                    # authored path below keeps the tag, alongside the
                    # ``bounded_dag_node`` that shows where its plan came from.
                    "operator",
                    "operator_priority",
                    "scope:bounded",
                    # Paired with "stage_transition:skip" on purpose. Twenty
                    # lines below, ``_prepare_persist`` REJECTS a Planner node
                    # that sets ``skip_stage_transition`` without
                    # ``require_independent_review`` as "an invalid review-only
                    # stage transition contract" — and this item was setting
                    # exactly that combination, so the Manager was exempting
                    # itself from the contract it enforces on the Planner.
                    #
                    # Untagged, ``round_self_review`` closes the mission the
                    # first time the Engineer writes MILESTONE_STATUS=DONE, with
                    # no second pair of eyes; the observed run settled a claimed
                    # proof of an open conjecture that way, in one round. It
                    # also inverted the skip: ``_should_run_stage_transition``
                    # only honors ``skip_stage_transition`` alongside
                    # ``require_independent_review`` on a bounded scope, so
                    # without this tag the mission fell through to the
                    # ``review_source == "engineer_self_review"`` arm and ran
                    # the stage transition the tag exists to prevent.
                    "review:required",
                    "stage_transition:skip",
                ],
                iterate=False,
                iteration_max_cycles=1,
                context_refs=_merge_context_refs(context_refs),
                original_objective=execution_body,
                manager_decision=decision_evidence(division) or {"routed": True},
            )
            mem.backlog.add(item)
            persisted["item"] = item
            try:
                from ..life.event_log import JsonlEventSink

                JsonlEventSink(None, life_dir=Path(life_dir)).append({
                    "type": "life.planner.task_added",
                    "item_id": item.id,
                    "title": item.title,
                    "objective": item.objective,
                    "deps": [],
                    "priority": item.priority,
                    "source": "manager_operator",
                    "operator_priority": True,
                })
            except Exception:  # noqa: BLE001 - backlog persistence is authoritative
                pass
            return item

        try:
            execution_body = front_door.manager_continuous_handoff(
                mem,
                body,
                chat_state,
                root_task_id=root_task_id,
                cancelled=cancelled,
                prepared_handoff=prepared_handoff,
                persist=_persist_operator_priority_item,
            )
        except Exception:
            if pending_auto_promote:
                chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
                    "continuous"
                ] = False
                chat_state["continuous_objective"] = ""
            raise
        item = persisted.get("item")
        if item is None:
            item = _persist_operator_priority_item(
                execution_body,
                getattr(prepared_handoff, "decision", None),
            )
        chat_state["last_objective"] = execution_body
        chat_state["continuous_objective"] = execution_body
        front_door._maybe_name_session(
            chat_state,
            execution_body,
            promote_task_name=True,
        )
        alive, pid = _daemon_status(life_dir)
        return item, alive, pid

    planned: dict[str, Any] = {}

    def _hydrate_context_refs(nodes: list[Any]) -> dict[str, list[dict[str, Any]]]:
        from ..core.campaign_workdir import resolve_task_workdir
        from ..planner.planner import hydrate_task_context_refs

        workdir = _resolve_manager_workdir(mem)
        hydrated_refs: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            try:
                raw_refs = list(getattr(node, "context_refs", ()) or ())
                try:
                    context_root = resolve_task_workdir(
                        workdir,
                        getattr(node, "execution_workdir", ""),
                    )
                except ValueError:
                    if (
                        str(getattr(node, "execution_workdir", "") or "").strip()
                        and list(getattr(node, "deps", ()) or ())
                        and not raw_refs
                    ):
                        context_root = None
                    else:
                        raise
                hydrated_refs[node.key] = (
                    []
                    if context_root is None
                    else hydrate_task_context_refs(raw_refs, context_root)
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
        from ..life.supervisor.backlog_guard import decision_evidence

        manager_decision = decision_evidence(_division) or {"routed": True}
        learned_candidate = (
            manager_decision.get("learned_vertical_status") == "candidate"
        )
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
        from ..core.campaign_workdir import (
            resolve_task_workdir,
            task_workdir_provenance,
        )
        from ..skills.stage_machine import current_stage

        campaign_workdir = _resolve_manager_workdir(mem)
        for index, node in enumerate(nodes):
            requested_workdir = str(
                getattr(node, "execution_workdir", "") or ""
            ).strip()
            persisted_workdir = task_workdir_provenance(
                campaign_workdir,
                requested_workdir,
            )
            try:
                node_workdir = resolve_task_workdir(
                    campaign_workdir,
                    requested_workdir,
                )
            except ValueError:
                raw_refs = list(getattr(node, "context_refs", ()) or ())
                if requested_workdir and list(node.deps) and not raw_refs:
                    # Preserve the existing deferred-clone contract: a dependency
                    # may create this workdir before the node becomes runnable.
                    node_workdir = campaign_workdir
                else:
                    raise
            stage = current_stage(node_workdir)
            stage_closing = bool(getattr(node, "stage_closing", False))
            require_review = bool(
                getattr(node, "require_independent_review", False)
            ) or learned_candidate
            skip_stage_transition = bool(
                getattr(node, "skip_stage_transition", False)
            )
            item_context_refs = _merge_context_refs(
                hydrated_refs.get(node.key, []),
                context_refs,
            )
            node_manager_decision = dict(manager_decision)
            node_vertical = str(getattr(node, "vertical", "") or "").strip()
            if node_vertical:
                from ..skills.vertical_select import (
                    UnknownVerticalError,
                    require_vertical,
                )

                try:
                    require_vertical(node_vertical, node_workdir)
                except UnknownVerticalError as exc:
                    raise front_door.ManagerHandoffError(
                        f"bounded Planner selected unknown vertical {node_vertical!r}"
                    ) from exc
                node_manager_decision["vertical"] = node_vertical
                node_manager_decision["route_source"] = "planner"
                node_manager_decision["routed"] = True
            item = BacklogItem.new(
                item_id=ids[node.key],
                title=str(node.title).replace("`", ""),
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
                    *(
                        ["skill_changes:allowed"]
                        if bool(getattr(node, "allow_skill_changes", False))
                        else []
                    ),
                    *([f"stage:{stage}"] if stage else []),
                ],
                iterate=False,
                iteration_max_cycles=1,
                deps=[ids[dep] for dep in node.deps],
                plan_id=plan_id,
                plan_version=1,
                node_key=node.key,
                context_refs=item_context_refs,
                work_kind=str(getattr(node, "work_kind", "") or ""),
                acceptance_check=str(getattr(node, "acceptance_check", "") or ""),
                plan_hypothesis=str(getattr(node, "hypothesis", "") or ""),
                goal_contribution=str(
                    getattr(node, "goal_contribution", "") or ""
                ),
                expected_regressions=str(
                    getattr(node, "expected_regressions", "") or ""
                ),
                decision_rule=str(getattr(node, "decision_rule", "") or ""),
                execution_workdir=persisted_workdir,
                non_goals=list(getattr(node, "non_goals", ()) or ()),
                manager_decision=node_manager_decision,
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
        front_door._maybe_name_session(
            chat_state,
            execution_body,
            promote_task_name=True,
        )
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
        chat_state.pop("_frontdoor_lifetime", "bounded") or "bounded"
    ).strip().lower()
    if lifetime not in {"bounded_increment", "bounded", "standing"}:
        lifetime = "bounded"
    normalized_workflow = str(workflow_mode or "").strip().lower()
    if lifetime == "bounded_increment" or (
        lifetime == "bounded" and normalized_workflow != "staged"
    ):
        chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
            "continuous"
        ] = False
        chat_state["continuous_objective"] = ""
        chat_state.pop("_continuous_open_ended", None)
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
    # default="codex": this value is only ever compared against "memory" (see
    # daemon.state.continuous_mode_error, which rejects continuous mode on the
    # in-process test backend). Any real backend passes the same gate, so an
    # unconfigured host must not fail an operator's chat turn here.
    backend = (
        str(daemon_status.life_backend or "")
        if daemon_status.alive
        else resolve_role_backend("", default="codex")
    )
    error = continuous_mode_error(
        backend or resolve_role_backend("", default="codex"), True, body
    )
    if error:
        raise front_door.ManagerHandoffError(error)
    persisted = read_continuous_state(life_dir)
    if persisted.enabled and persisted.objective.strip():
        chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
            "continuous"
        ] = True
        chat_state["continuous_objective"] = persisted.objective
        chat_state["_continuous_open_ended"] = persisted.open_ended
        chat_state.pop("_continuous_pending_manager_handoff", None)
        return True

    chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))[
        "continuous"
    ] = True
    chat_state["_continuous_pending_manager_handoff"] = True
    chat_state["_continuous_open_ended"] = lifetime == "standing"
    chat_state["continuous_objective"] = ""
    return True


__all__ = [
    "DEFAULT_MANAGER_CONFIG",
    "enqueue_mission",
    "maybe_promote_to_continuous",
    "resume_done_lifecycle_for_team_dispatch",
]
