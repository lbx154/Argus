"""Manager-owned task lifetime and durable dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..apps._life_actions import DEFAULT_LIFE_CONFIG
from ..core.operator_messages import brief_title
from . import front_door

DEFAULT_MANAGER_CONFIG = DEFAULT_LIFE_CONFIG


class MissionPersistenceError(front_door.ManagerHandoffError):
    """A Manager-authored mission could not be written to the backlog."""


def _resolve_manager_workdir(mem: Any) -> Path:
    from ..core.session import read_session_meta, resolve_session_workdir

    life_dir = Path(front_door._life_dir_for(mem))
    global_root = getattr(mem, "global_root", None)
    root = Path(global_root) if global_root is not None else life_dir.parent.parent
    meta = read_session_meta(root, life_dir.name)
    if meta is not None and (
        meta.workdir.strip() or meta.cwd.strip()
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


def _campaign_stage(state_root: Path | str, workdir: Path | str) -> str:
    """Stage a new backlog item is bound to, read where the stage is kept.

    The supervisor reads the campaign stage from the project state root and
    replays reviewed work only for rows tagged with that stage. Reading the
    team workdir instead (which has no pipeline state of its own) tagged the
    item with the vertical's first stage, so its reviewed result was never
    replayed and the campaign could never certify.
    """
    from ..core.pipeline_state import pipeline_state_exists
    from ..skills.stage_machine import current_stage

    try:
        kept = pipeline_state_exists(state_root)
    except OSError:
        kept = False
    return current_stage(state_root if kept else workdir)


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


def _attachable_reference_deps(
    mem: Any,
    reference_deps: list[str] | None,
    *,
    exclude: set[str],
) -> list[str]:
    """Referenced backlog ids a NEW item may safely list as ``deps``.

    Only ids present in the backlog whose status can still satisfy a
    dependency are kept: ``done`` (immediately satisfied — the new item is
    ready at once) or a live status (the new item waits its turn). A dep on a
    failed/skipped/superseded/aborted task would make the new item
    permanently unsatisfiable and ``memory._cascade_blocked`` would skip it,
    so those ids — and ids that no longer exist — are dropped here. The
    reference expansion already told the operator/model which ones were
    dropped (``webapi.map_references``); this is the authoritative re-check
    at persist time.
    """
    if not reference_deps:
        return []
    from ..life.memory import _TERMINAL_STATUSES

    try:
        statuses = {
            str(item.id): str(item.status)
            for item in mem.backlog.history()
        }
    except Exception:  # noqa: BLE001 - deps are an attachment, not the mission
        return []
    attachable: list[str] = []
    for dep in dict.fromkeys(str(dep) for dep in reference_deps):
        if not dep or dep in exclude:
            continue
        status = statuses.get(dep)
        if status is None:
            continue
        if status in _TERMINAL_STATUSES and status != "done":
            continue
        attachable.append(dep)
    return attachable


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


def _continuous_backend_error(life_dir: Path, body: str) -> str:
    from ..core.knobs import resolve_role_backend
    from ..daemon.life_worker import continuous_mode_error, read_daemon_status

    daemon_status = read_daemon_status(life_dir)
    # Only compared against "memory" (the in-process test backend that cannot
    # plan), so an unconfigured host must not fail an operator's turn here.
    backend = (
        str(daemon_status.life_backend or "") if daemon_status.alive else ""
    ) or resolve_role_backend("", default="codex")
    return continuous_mode_error(backend, True, body)


def enqueue_mission(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    prepared_handoff: front_door.PreparedManagerHandoff | None = None,
    context_refs: list[dict[str, str]] | None = None,
    reference_deps: list[str] | None = None,
) -> tuple[Any, bool, int | None]:
    """Run one operator TEAM task inside a Planner-run campaign.

    There is one dispatch topology. The task becomes one reviewed operator
    item in a durable campaign whose Planner sequences what follows and, for
    finite work, declares the campaign done. With no campaign running, the
    task arms one: finite unless the front door read standing intent. A direct
    task joins a running campaign without replacing it; staged or standing
    work replaces the running campaign's objective.
    """
    domain_display = chat_state.pop("_domain_display_objective", {})
    display_objective = (str(domain_display.get("objective") or "")
                         if domain_display.get("execution_task") == body else "")
    display_title = (str(domain_display.get("title") or "")
                     if domain_display.get("execution_task") == body else "")
    if prepared_handoff is None:
        prepared_handoff = front_door.prepare_manager_execution_task(
            mem, body, chat_state, root_task_id=root_task_id,
        )
    standing = str(
        chat_state.pop("_team_lifetime", "") or ""
    ).strip().lower() == "standing"
    direct = not standing and str(
        getattr(prepared_handoff.decision, "workflow_mode", "") or ""
    ).strip().lower() == "direct"
    life_dir = Path(front_door._life_dir_for(mem))
    from ..daemon.state import read_continuous_state

    campaign = read_continuous_state(life_dir)
    campaign_running = campaign.enabled and bool(campaign.objective.strip())
    persisted: dict[str, Any] = {}

    def _persist(execution_body: str, division: Any) -> Any:
        if callable(cancelled) and cancelled():
            raise front_door.ManagerHandoffError(
                "Manager request cancelled before backlog commit"
            )
        from ..life.memory import BacklogItem
        from ..life.supervisor.backlog_guard import decision_evidence

        contextual_body = (
            "[CURRENT OPERATOR MESSAGE]" in body
            and (
                "[RECENT CONVERSATION CONTEXT" in body
                or "[BOUNDED TASK CONTEXT" in body
            )
        )
        title_body = display_title or display_objective or (
            execution_body if direct or contextual_body else body
        )
        compact = " ".join(title_body.split()).replace("`", "")
        # The Manager's short topic names the task wherever it is mentioned;
        # without one, the request's opening clause does, never the whole text.
        topic = str(getattr(prepared_handoff.decision, "session_title", "") or "")
        manager_decision = decision_evidence(division) or {"routed": True}
        # The roles read the Manager's brief; the operator goes on reading
        # their own request.
        shown = display_objective or str(prepared_handoff.public_objective or "")
        if shown.strip() and shown.strip() != execution_body.strip():
            manager_decision["display_objective"] = shown
        plan = getattr(prepared_handoff.decision, "plan", None)
        if plan:
            # The one record of the Manager's plan: the held-launch card reads it here.
            manager_decision["plan"] = dict(plan)
        if direct:
            # One reviewed, stage-closing mission: the same item the campaign
            # supervisor queues for a finite direct objective, so its certified
            # review finishes the campaign instead of queueing a second run.
            manager_decision["route_source"] = "manager"
            stage = _campaign_stage(life_dir, _resolve_manager_workdir(mem))
            kind_tags = [
                "manager_direct",
                "stage_closing",
                *([f"stage:{stage}"] if stage else []),
            ]
        else:
            # Not "planner": nothing decomposed this raw operator task. Review
            # is required and the stage stays with the campaign: the Reviewer
            # closes the item, and the Planner decides what the stage needs.
            kind_tags = ["operator", "operator_priority", "stage_transition:skip"]
        head_priority = min(
            (row.priority for row in mem.backlog.pending()), default=100,
        )
        # The person's required step (必经步骤) the Manager's ticket names: the
        # task carries out that step only, tied to it, so the Planner plans the
        # later steps in order and a checkpoint can pause between them.
        from ..core.waypoints import read_route

        step_id = str(getattr(prepared_handoff.decision, "waypoint_id", "") or "")
        step = next((row for row in read_route(life_dir) if row.id == step_id), None) if step_id else None
        objective, acceptance_check = execution_body, ""
        if step is not None:
            from ..core.waypoints import scope_to_step

            # The original objective stays the Manager's own words: the
            # campaign recognises its direct objective as already handed out.
            objective, acceptance_check = scope_to_step(execution_body, step)
            # One step is not the whole stage: it closes nothing, and the
            # Planner plans the steps after it (eval w1: a stage-closing first
            # step was rejected at completion and re-checked for nothing).
            kind_tags = ["operator", "operator_priority", "stage_transition:skip"]
        item = BacklogItem.new(
            item_id=root_task_id,
            title=brief_title(display_title or topic or compact),
            objective=objective,
            priority=min(head_priority - 1, -1),
            tags=["manager", "scope:bounded", "review:required", *kind_tags],
            iterate=False,
            iteration_max_cycles=1,
            # Operator-quoted map nodes attach the new work where the operator
            # pointed instead of leaving it dangling at the end of the graph.
            deps=_attachable_reference_deps(
                mem, reference_deps, exclude={str(root_task_id or "")},
            ),
            context_refs=_merge_context_refs(context_refs),
            original_objective=execution_body,
            manager_decision=manager_decision,
            acceptance_check=acceptance_check,
            waypoint_id=step.id if step is not None else "",
        )
        try:
            mem.backlog.add(item)
        except OSError as exc:
            chat_state.setdefault("_pending_missions", []).append((item,))
            raise MissionPersistenceError(
                f"Could not persist mission to {mem.backlog.path}: {exc}. "
                "The mission remains pending in memory and was not dispatched."
            ) from exc
        persisted["item"] = item
        from ..life.event_log import JsonlEventSink

        event = {
            "type": "life.planner.task_added",
            "item_id": item.id,
            "title": item.title,
            "objective": item.objective,
            "deps": list(item.deps),
            "priority": item.priority,
            "source": "manager_direct" if direct else "manager_operator",
        }
        if not direct:
            event["operator_priority"] = True
        sink = JsonlEventSink(None, life_dir=life_dir)
        sink.append(event)
        from ..core.autonomy import record_assumptions

        record_assumptions(life_dir, prepared_handoff.decision.assumptions, item_id=item.id, source="route")
        return item

    if direct and campaign_running:
        front_door.manager_bounded_handoff(
            mem,
            body,
            chat_state,
            _persist,
            root_task_id=root_task_id,
            cancelled=cancelled,
            prepared_handoff=prepared_handoff,
        )
        objective = campaign.objective
    else:
        error = _continuous_backend_error(life_dir, body)
        if error:
            exc = front_door.ManagerHandoffError(error)
            prepared_handoff.failed(exc)
            raise exc
        chat_state["_continuous_open_ended"] = (
            campaign.open_ended if campaign_running else standing
        )
        objective = front_door.manager_continuous_handoff(
            mem,
            body,
            chat_state,
            root_task_id=root_task_id,
            cancelled=cancelled,
            prepared_handoff=prepared_handoff,
            persist=_persist,
        )
    item = persisted["item"]
    chat_state.setdefault("config", dict(DEFAULT_MANAGER_CONFIG))["continuous"] = True
    chat_state["continuous_objective"] = objective
    chat_state["last_objective"] = item.objective
    front_door._maybe_name_session(
        chat_state,
        item.objective,
        suggested_name=display_title,
        promote_task_name=True,
    )
    alive, pid = _daemon_status(life_dir)
    return item, alive, pid


__all__ = [
    "DEFAULT_MANAGER_CONFIG",
    "MissionPersistenceError",
    "enqueue_mission",
    "resume_done_lifecycle_for_team_dispatch",
]
