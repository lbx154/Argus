"""Manager-owned task lifetime and durable dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..apps._life_actions import DEFAULT_LIFE_CONFIG, add_backlog_item
from . import front_door

DEFAULT_MANAGER_CONFIG = DEFAULT_LIFE_CONFIG


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
    from ..core.session import read_session_meta
    from ..life.project_lifecycle import (
        infer_observable_status,
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
    observable_root = (
        Path(meta.launch_cwd)
        if meta is not None and meta.launch_cwd and Path(meta.launch_cwd).exists()
        else life_dir
    )
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
    budget: float = 30.0,
    root_task_id: str | None = None,
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

    def _persist(execution_body: str, _division: Any) -> Any:
        pending = mem.backlog.pending()
        head_priority = min((item.priority for item in pending), default=100)
        item = add_backlog_item(
            mem,
            execution_body,
            item_id=root_task_id,
            priority=min(head_priority - 1, -1),
            iterate=iterate,
            iteration_max_cycles=max_cycles,
            iteration_budget_usd=budget,
        )
        front_door._maybe_name_session(chat_state, execution_body)
        return item

    item = front_door.manager_bounded_handoff(
        mem,
        body,
        chat_state,
        _persist,
        root_task_id=root_task_id,
    )
    chat_state["last_objective"] = item.objective
    alive, pid = _daemon_status(life_dir)
    return item, alive, pid


def maybe_promote_to_continuous(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> bool:
    """Let Manager choose STANDING lifetime for an open-ended team task."""
    frontdoor_lifetime = str(
        chat_state.pop("_frontdoor_lifetime", "") or ""
    ).strip().lower()
    if frontdoor_lifetime == "bounded":
        return False
    if frontdoor_lifetime == "standing":
        is_standing = True
    else:
        runner = front_door._ensure_manager_runner(chat_state, mem)
        classify = getattr(runner, "classify_needs_continuous", None)
        if runner is None or not callable(classify):
            return False

        is_standing = True
        try:
            if root_task_id is None or not front_door._accepts_keyword(
                classify,
                "root_task_id",
            ):
                is_standing = bool(classify(body))
            else:
                is_standing = bool(classify(body, root_task_id=root_task_id))
            if not is_standing:
                return False
        except Exception:  # noqa: BLE001 - substantive team work defaults to standing
            pass

    from ..daemon.life_worker import continuous_mode_error

    backend = str(chat_state.get("backend") or "codex")
    if continuous_mode_error(backend, True, body):
        return False

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
