"""Deterministic dependency/resource list scheduling, without research judgments."""

from __future__ import annotations

from .timeline_models import Proposal, Task


def _slot(start, duration, demand, reservations, capacity):
    """First feasible non-preemptive interval; release events are candidate starts."""
    if duration == 0:
        return start
    candidates = sorted({start, *(end for _, end, _ in reservations if end >= start)})
    for candidate in candidates:
        finish = candidate + duration
        points = {
            candidate,
            *(begin for begin, end, _ in reservations if candidate < begin < finish),
        }
        if all(
            all(
                units
                + sum(
                    used.get(name, 0) for begin, end, used in reservations if begin <= point < end
                )
                <= capacity[name]
                for name, units in demand.items()
            )
            for point in points
        ):
            return candidate
    raise ValueError("no feasible resource interval")


def schedule(
    proposal: Proposal, capacity: dict[str, int], now: float, scenario: str, excluded: set[str]
) -> dict:
    reservations: list[tuple[float, float, dict[str, int]]] = []
    rows: dict[str, dict] = {}
    blocked: list[dict] = []
    # Actual running allocations must be reserved before scheduling pending work.
    for task in proposal.tasks:
        if task.status == "running":
            end = now + task.remaining.hours(scenario)
            reservations.append((now, end, task.resources))
    for name, limit in capacity.items():
        if (
            sum(task.resources.get(name, 0) for task in proposal.tasks if task.status == "running")
            > limit
        ):
            raise ValueError(f"running tasks exceed {name} capacity")
    for task in proposal.tasks:
        if task.id in excluded:
            continue
        if task.status in {"completed", "failed"}:
            start, end, wait = task.actual_start, task.actual_finish, 0.0
        elif task.status == "running":
            start, end, wait = task.actual_start, now + task.remaining.hours(scenario), 0.0
        else:
            unavailable = [
                dep for dep in task.depends_on if dep not in rows or rows[dep]["status"] == "failed"
            ]
            if task.status == "blocked" or unavailable:
                blocked.append(
                    {
                        "id": task.id,
                        "reason": task.reason
                        if task.status == "blocked"
                        else "Unresolved dependencies: " + ", ".join(unavailable),
                    }
                )
                continue
            ready = max([now, *(rows[dep]["finish_hours"] for dep in task.depends_on)])
            duration = task.duration.hours(scenario)
            start = _slot(ready, duration, task.resources, reservations, capacity)
            end, wait = start + duration, start - ready
            reservations.append((start, end, task.resources))
        rows[task.id] = dict(
            id=task.id,
            title=task.title,
            phase=task.phase,
            status=task.status,
            start_hours=start,
            finish_hours=end,
            resource_wait_hours=wait,
            resources=task.resources,
            depends_on=list(task.depends_on),
            basis=task.basis,
            difficulty=task.difficulty,
            reason=task.reason,
            evidence=list(task.evidence),
            duration_hours=[task.duration.lower, task.duration.likely, task.duration.upper],
            execution_option_id=task.execution_option_id,
        )
    # A failed task remains evidence, never something to schedule again. A revised
    # proposal can retire it (optional=true) and add replacement work with new IDs.
    unresolved = blocked or [t for t in proposal.tasks if t.status == "failed" and not t.optional]
    finish = max((row["finish_hours"] for row in rows.values()), default=now)
    return {
        "schedule": list(rows.values()),
        "blocked_tasks": blocked,
        "finish_hours": None if unresolved else finish,
        "scheduled_through_hours": finish,
    }


def optional_candidates(tasks: tuple[Task, ...]) -> list[str]:
    """Only explicitly optional, unstarted nodes outside required dependency closure."""
    protected = {task.id for task in tasks if not task.optional or task.status != "pending"}
    by_id = {task.id: task for task in tasks}
    todo = list(protected)
    while todo:
        for dep in by_id[todo.pop()].depends_on:
            if dep not in protected:
                protected.add(dep)
                todo.append(dep)
    # Defer dependents first so remaining tasks never depend on a deferred node.
    return [task.id for task in reversed(tasks) if task.id not in protected]


def forecast(
    proposal: Proposal,
    capacity: dict[str, int],
    now: float,
    deadline: float | None,
    defer_optional: bool,
) -> dict:
    excluded: set[str] = set()
    nominal = schedule(proposal, capacity, now, "expected", excluded)
    if defer_optional and deadline is not None:
        for key in optional_candidates(proposal.tasks):
            if nominal["finish_hours"] is not None and nominal["finish_hours"] <= deadline:
                break
            excluded.add(key)
            nominal = schedule(proposal, capacity, now, "expected", excluded)
    lower = schedule(proposal, capacity, now, "lower", excluded)["finish_hours"]
    upper = schedule(proposal, capacity, now, "upper", excluded)["finish_hours"]
    expected = nominal.pop("finish_hours")
    # Greedy packing can be nonmonotone; expose an envelope over the scenarios.
    interval = (
        None
        if expected is None
        else dict(
            lower=min(lower, expected, upper), expected=expected, upper=max(lower, expected, upper)
        )
    )
    return dict(
        id=proposal.id,
        title=proposal.title,
        **nominal,
        finish_hours=interval,
        remaining_hours=None if expected is None else max(0, expected - now),
        deadline_gap_hours=None
        if deadline is None or expected is None
        else max(0, expected - deadline),
        deferred_task_ids=sorted(excluded),
        assumptions=list(proposal.assumptions),
        failed_tasks=[
            {"id": t.id, "reason": t.reason, "evidence": list(t.evidence)}
            for t in proposal.tasks
            if t.status == "failed"
        ],
    )
