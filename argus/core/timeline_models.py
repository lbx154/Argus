"""Validated inputs for advisory research forecasts; durations are elapsed hours."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return result


def label(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return tuple(label(item, name) for item in value)


def resources(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("resources must be an object of positive integer capacities")
    result = {}
    for key, count in value.items():
        label(key, "resource name")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("resource units must be positive integers")
        result[key] = count
    return result


@dataclass(frozen=True)
class Duration:
    lower: float
    likely: float
    upper: float

    @classmethod
    def parse(cls, value: Any, name: str) -> Duration:
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError(f"{name} requires [lower, most_likely, upper] hours")
        a, m, b = (number(v, name) for v in value)
        if not a <= m <= b:
            raise ValueError(f"{name} requires lower <= most_likely <= upper")
        return cls(a, m, b)

    def hours(self, scenario: str) -> float:
        if scenario == "expected":
            return self.likely + (self.lower - self.likely) / 6 + (self.upper - self.likely) / 6
        return getattr(self, scenario)


@dataclass(frozen=True)
class ExecutionOption:
    id: str
    title: str
    duration: Duration
    resources: dict[str, int]
    basis: str
    tradeoff: str
    preserves_acceptance: bool

    @classmethod
    def parse(cls, row: Any) -> ExecutionOption:
        if not isinstance(row, dict):
            raise ValueError("execution option must be an object")
        preserves = row.get("preserves_acceptance")
        if not isinstance(preserves, bool):
            raise ValueError("execution option requires preserves_acceptance boolean")
        key = label(row.get("id"), "execution option id")
        if key == "standard":
            raise ValueError("standard is reserved for the original execution option")
        return cls(
            key,
            label(row.get("title"), "option title"),
            Duration.parse(row.get("duration_hours"), "option duration_hours"),
            resources(row.get("resources", {})),
            label(row.get("basis"), "option basis"),
            label(row.get("tradeoff"), "option tradeoff"),
            preserves,
        )


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    phase: str
    depends_on: tuple[str, ...]
    resources: dict[str, int]
    duration: Duration
    basis: str
    optional: bool
    status: str
    actual_start: float | None
    actual_finish: float | None
    remaining: Duration | None
    reason: str
    evidence: tuple[str, ...]
    difficulty: str
    execution_options: tuple[ExecutionOption, ...] = ()
    execution_option_id: str = "standard"

    @classmethod
    def parse(cls, row: Any, capacities: dict[str, int], now: float) -> Task:
        if not isinstance(row, dict):
            raise ValueError("each task must be an object")
        key = label(row.get("id"), "task id")
        demand = resources(row.get("resources", {}))
        status = row.get("status", "pending")
        if not isinstance(status, str) or status not in {
            "pending",
            "running",
            "completed",
            "failed",
            "blocked",
        }:
            raise ValueError(f"{key}: unsupported status")
        for resource, units in demand.items():
            if status not in {"completed", "failed"} and units > capacities.get(resource, 0):
                raise ValueError(f"{key}: insufficient capacity for {resource}")
        optional = row.get("optional", False)
        if not isinstance(optional, bool):
            raise ValueError(f"{key}: optional must be boolean")
        start = finish = None
        remaining = None
        if status in {"running", "completed", "failed"}:
            start = number(row.get("actual_start_hours"), f"{key}.actual_start_hours")
            if start > now:
                raise ValueError(f"{key}: actual start is in the future")
        if status in {"completed", "failed"}:
            finish = number(row.get("actual_finish_hours"), f"{key}.actual_finish_hours")
            if start is None or not start <= finish <= now:
                raise ValueError(f"{key}: invalid actual finish")
        if status == "running":
            remaining = Duration.parse(row.get("remaining_hours"), f"{key}.remaining_hours")
        reason = row.get("reason", "")
        difficulty = row.get("difficulty", "")
        if not isinstance(reason, str) or not isinstance(difficulty, str):
            raise ValueError(f"{key}: reason and difficulty must be text")
        if status in {"failed", "blocked"} and not reason.strip():
            raise ValueError(f"{key}: {status} requires a reason")
        option_rows = row.get("execution_options", [])
        if not isinstance(option_rows, list) or len(option_rows) > 8:
            raise ValueError("execution_options must be a list of at most 8 alternatives")
        options = tuple(ExecutionOption.parse(option) for option in option_rows)
        if len({option.id for option in options}) != len(options):
            raise ValueError(f"{key}: duplicate execution option id")
        return cls(
            key,
            label(row.get("title"), f"{key}.title"),
            label(row.get("phase"), f"{key}.phase"),
            strings(row.get("depends_on", []), f"{key}.depends_on"),
            demand,
            Duration.parse(row.get("duration_hours"), f"{key}.duration_hours"),
            label(row.get("basis"), f"{key}.basis"),
            optional,
            status,
            start,
            finish,
            remaining,
            reason.strip(),
            strings(row.get("evidence", []), f"{key}.evidence"),
            difficulty,
            options,
            label(row.get("execution_option_id", "standard"), "execution_option_id"),
        )


@dataclass(frozen=True)
class Proposal:
    id: str
    title: str
    tasks: tuple[Task, ...]
    assumptions: tuple[str, ...]

    @classmethod
    def parse(cls, row: Any, capacities: dict[str, int], now: float) -> Proposal:
        if not isinstance(row, dict):
            raise ValueError("each proposal must be an object")
        rows = row.get("tasks")
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            raise ValueError("proposal tasks must contain 1 to 200 tasks")
        tasks = tuple(Task.parse(task, capacities, now) for task in rows)
        by_id = {task.id: task for task in tasks}
        if len(by_id) != len(tasks):
            raise ValueError("duplicate task id")
        ordered: list[Task] = []
        seen: set[str] = set()
        while len(ordered) < len(tasks):
            ready = [task for task in tasks if task.id not in seen and set(task.depends_on) <= seen]
            if not ready:
                raise ValueError("task dependencies contain a cycle or unknown task")
            for task in ready:
                ordered.append(task)
                seen.add(task.id)
        for task in tasks:
            if task.status in {"running", "completed", "failed"}:
                for dep in task.depends_on:
                    prior = by_id[dep]
                    if (prior.status != "completed" or prior.actual_finish is None
                            or task.actual_start is None or prior.actual_finish > task.actual_start):
                        raise ValueError(f"{task.id}: executed task has an unfinished dependency")
        return cls(
            label(row.get("id"), "proposal id"),
            label(row.get("title"), "title"),
            tuple(ordered),
            strings(row.get("assumptions", []), "assumptions"),
        )
