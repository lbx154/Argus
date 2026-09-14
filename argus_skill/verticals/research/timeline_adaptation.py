"""Deadline-driven choice among declared execution options, never invented speedups."""

from __future__ import annotations

from dataclasses import replace

from .timeline_models import Proposal
from .timeline_schedule import forecast


def critical_order(proposal: Proposal) -> Proposal:
    children = {task.id: [] for task in proposal.tasks}
    for task in proposal.tasks:
        for parent in task.depends_on:
            children[parent].append(task.id)
    lengths: dict[str, float] = {}
    for task in reversed(proposal.tasks):
        duration = task.remaining or task.duration
        lengths[task.id] = duration.hours("expected") + max(
            (lengths[child] for child in children[task.id]), default=0
        )
    ordered, seen = [], set()
    while len(ordered) < len(proposal.tasks):
        ready = [t for t in proposal.tasks if t.id not in seen and set(t.depends_on) <= seen]
        chosen = max(ready, key=lambda t: lengths[t.id])
        ordered.append(chosen)
        seen.add(chosen.id)
    return replace(proposal, tasks=tuple(ordered))


def _score(result):
    end = result["finish_hours"]
    if end is None:
        return (float("inf"), float("inf"))
    return (end["expected"], sum(r["finish_hours"] - r["start_hours"] for r in result["schedule"]))


def adapt(
    proposal: Proposal,
    capacity: dict[str, int],
    now: float,
    deadline: float | None,
    defer_optional: bool,
) -> dict:
    """Greedy, bounded list scheduling. Options declare equivalent acceptance scope.

    Recompute from the source proposal on each call so relaxing the deadline can
    restore the preferred implementation and optional work. No input mutation.
    """
    baseline = forecast(proposal, capacity, now, deadline, defer_optional)
    current, result = proposal, baseline
    choices = {}
    if deadline is not None and _score(result)[0] > deadline and result["finish_hours"]:
        reordered = critical_order(current)
        candidate = forecast(reordered, capacity, now, deadline, defer_optional)
        if _score(candidate) < _score(result):
            current, result = reordered, candidate
        # Each pending task can switch at most once. Consider all its declared
        # options at that decision; shared critical paths may require several
        # switches before the project's makespan decreases.
        while _score(result)[0] > deadline:
            best = None
            for task in current.tasks:
                if (
                    task.status != "pending"
                    or task.id in choices
                    or task.id in result["deferred_task_ids"]
                ):
                    continue
                for option in task.execution_options:
                    if not option.preserves_acceptance or any(
                        units > capacity.get(resource, 0)
                        for resource, units in option.resources.items()
                    ):
                        continue
                    changed = replace(
                        task,
                        duration=option.duration,
                        resources=option.resources,
                        basis=option.basis,
                        execution_option_id=option.id,
                    )
                    attempt = critical_order(
                        replace(
                            current,
                            tasks=tuple(changed if t.id == task.id else t for t in current.tasks),
                        )
                    )
                    projected = forecast(attempt, capacity, now, deadline, defer_optional)
                    if _score(projected) >= _score(result):
                        continue
                    point = _score(projected)[0]
                    # Once an option fits, prefer the smallest sufficient time
                    # saving; otherwise reduce makespan, then aggregate effort.
                    score = (0, -point) if point <= deadline else (1, *_score(projected))
                    if best is None or score < best[0]:
                        best = (score, attempt, projected, task.id, option)
            if best is None:
                break
            _, current, result, task_id, option = best
            choices[task_id] = option

    original = {t.id: t for t in proposal.tasks}
    old_rows = {row["id"]: row for row in baseline["schedule"]}
    changes = []
    for row in result["schedule"]:
        task = original[row["id"]]
        if option := choices.get(task.id):
            changes.append(
                dict(
                    kind="execution_option",
                    id=task.id,
                    title=task.title,
                    option_title=option.title,
                    reason=option.basis,
                    tradeoff=option.tradeoff,
                    from_duration_hours=[
                        task.duration.lower,
                        task.duration.likely,
                        task.duration.upper,
                    ],
                    to_duration_hours=row["duration_hours"],
                    from_resources=task.resources,
                    to_resources=option.resources,
                )
            )
        elif task.id in old_rows and (row["start_hours"], row["finish_hours"]) != (
            old_rows[task.id]["start_hours"],
            old_rows[task.id]["finish_hours"],
        ):
            changes.append(
                dict(
                    kind="rescheduled",
                    id=task.id,
                    title=task.title,
                    reason="依赖 / 资源顺序重排 · Dependency / resource rescheduling",
                    tradeoff="",
                    from_start_hours=old_rows[task.id]["start_hours"],
                    to_start_hours=row["start_hours"],
                )
            )
    for key in result["deferred_task_ids"]:
        changes.append(
            dict(
                kind="deferred",
                id=key,
                title=original[key].title,
                reason="延后已声明的可选工作 · Defer declared optional work",
                tradeoff="本轮计划不包含该项 · Outside this delivery schedule",
            )
        )
    end = result["finish_hours"]
    result["adaptation"] = dict(
        enabled=True,
        target_hours=deadline,
        baseline_finish_hours=baseline["finish_hours"],
        status="blocked"
        if end is None
        else "gap"
        if deadline is not None and end["expected"] > deadline
        else "fits",
        changes=changes,
        explanation="基于原 proposal 的依赖、资源与已声明替代方案重新安排；不缩放原估计。"
        " Replanned from dependencies, resources and declared options; estimates are not scaled.",
    )
    return result
