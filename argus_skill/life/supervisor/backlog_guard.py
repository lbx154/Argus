"""Catch backlog items that never went through the Manager.

`backlog.jsonl` is an ordinary file. An outer agent — Copilot, Claude Code,
whatever is driving the box — can append a row to it and the daemon will
execute that row as a mission. Nothing is corrupted, and that is the problem:
the item skipped `manager_bounded_handoff`, so no vertical was chosen, no
stage or target level was set, and the workflow mode defaulted.

The visible symptom is a Manager that appears to do nothing. A literature-review
task lands as a bare objective, the run proceeds under the default flow, and
the vertical the operator expected never engages. Nothing errors, so nothing
draws attention to it.

File permissions cannot close this — the agent has full filesystem access, and
writing to the backlog is a reasonable thing to want to do. So this does not
block the write. It marks items the Manager decided on, notices the ones it
did not, and routes those back through the Manager before they execute. The
back door becomes another way in through the front.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

log = logging.getLogger(__name__)

__all__ = [
    "DECISION_KEY",
    "decision_evidence",
    "describe_undecided",
    "ensure_manager_decision",
    "needs_manager_decision",
    "undecided_items",
]

#: Field on a backlog item recording that the Manager routed it.
DECISION_KEY = "manager_decision"


def decision_evidence(decision: Any) -> dict[str, Any]:
    """The parts of a Manager decision worth persisting on the item.

    Enough to tell later that a real routing happened and what it chose —
    not a copy of the whole decision object.
    """
    if decision is None:
        return {}
    fields = (
        "vertical",
        "stage",
        "workflow_mode",
        "research_target_level",
        "learned_vertical_status",
    )
    evidence = {
        name: str(getattr(decision, name, "") or "").strip()
        for name in fields
    }
    evidence = {name: value for name, value in evidence.items() if value}
    if bool(getattr(decision, "require_independent_review", True)):
        evidence["require_independent_review"] = True
    if evidence:
        evidence["routed"] = True
    return evidence


def needs_manager_decision(item: Any) -> bool:
    """Whether *item* reached the backlog without a Manager routing.

    Items created before this field existed also answer ``True``. That is
    deliberate: re-routing one costs a Manager call and removes the blindness,
    while assuming an old item was routed would preserve exactly the bug.
    """
    if item is None:
        return False
    decision = getattr(item, DECISION_KEY, None)
    if decision is None and isinstance(item, dict):
        decision = item.get(DECISION_KEY)
    return not (isinstance(decision, dict) and decision.get("routed"))


def undecided_items(items: Any) -> list[Any]:
    """Pending items that bypassed the Manager, in backlog order."""
    return [
        item
        for item in (items or [])
        if str(getattr(item, "status", "")) == "pending" and needs_manager_decision(item)
    ]


def describe_undecided(items: Any) -> str:
    """Operator-facing summary, or ``""`` when everything was routed."""
    pending = undecided_items(items)
    if not pending:
        return ""
    titles = ", ".join(
        str(getattr(item, "title", "") or getattr(item, "id", "?"))[:40]
        for item in pending[:3]
    )
    more = f" (+{len(pending) - 3} more)" if len(pending) > 3 else ""
    return (
        f"{len(pending)} backlog item(s) reached the queue without a Manager "
        f"decision: {titles}{more}. They were written directly rather than "
        "dispatched, so no vertical, stage, or target level was chosen. Each is "
        "routed through the Manager before it runs."
    )


def ensure_manager_decision(
    memory: Any,
    item: Any,
    chat_state: Any = None,
    *,
    manager: Any = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    vertical_root: Path | str | None = None,
) -> Any:
    """Route *item* through the Manager if it never was, and record that.

    ``vertical_root`` is the claimed node's canonical policy root. Long-running
    queue and journal state still belongs to ``memory.root``.

    Returns the item, with its objective replaced by the Manager's execution
    task when a routing happened. Failure to route is logged and the item is
    returned unchanged: a blind run is better than a stalled queue, and the
    diagnostic surface already reports the item as undecided.
    """
    if not needs_manager_decision(item):
        decision = getattr(item, DECISION_KEY, None)
        vertical = (
            str(decision.get("vertical") or "").strip()
            if isinstance(decision, dict)
            else ""
        )
        if not vertical:
            return item
        from ...skills.vertical_select import UnknownVerticalError, require_vertical
        from ...verticals._data_domain import materialize_learned_data_domain

        state_root = Path(getattr(memory, "root", ".")).expanduser()
        policy_root = Path(vertical_root or state_root).expanduser()
        learned_root = Path(
            getattr(memory, "global_root", None) or state_root
        ).expanduser()
        materialize_learned_data_domain(
            learned_root,
            policy_root,
            vertical,
        )
        try:
            require_vertical(vertical, policy_root)
        except UnknownVerticalError:
            log.warning(
                "backlog guard: routed vertical %s is unavailable; rerouting item %s",
                vertical,
                getattr(item, "id", "?"),
            )
        else:
            return item

    objective = str(getattr(item, "objective", "") or "").strip()
    if not objective:
        return item

    try:
        from ...manager.front_door import prepare_manager_execution_task
    except Exception:  # noqa: BLE001 - never let this path break execution
        log.exception("backlog guard: manager front door unavailable")
        return item

    try:
        manager_runner = (
            SimpleNamespace(manager=manager) if manager is not None else None
        )
        prepared = prepare_manager_execution_task(
            memory,
            objective,
            dict(chat_state or {}),
            root_task_id=str(getattr(item, "id", "") or "") or None,
            ensure_runner=(
                (lambda _state, _memory: manager_runner)
                if manager_runner is not None
                else ensure_runner
            ),
        )
        execution_task = prepared.execution_task
        evidence = decision_evidence(getattr(prepared, "decision", None)) or {
            "routed": True
        }
    except Exception:  # noqa: BLE001
        log.exception(
            "backlog guard: could not route item %s through the Manager; running "
            "it as written",
            getattr(item, "id", "?"),
        )
        return item

    updates: dict[str, Any] = {DECISION_KEY: evidence}
    if execution_task and execution_task.strip() != objective:
        updates["objective"] = execution_task
    try:
        memory.backlog.update(getattr(item, "id", ""), **updates)
    except Exception:  # noqa: BLE001
        log.exception("backlog guard: could not persist the Manager decision")
        return item

    for key, value in updates.items():
        try:
            setattr(item, key, value)
        except Exception:  # noqa: BLE001
            pass
    log.info(
        "backlog guard: routed directly-written item %s through the Manager",
        getattr(item, "id", "?"),
    )
    return item
