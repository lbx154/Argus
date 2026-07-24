"""Compact Planner pass for Manager-authored bounded tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec


@dataclass(frozen=True)
class BoundedDagNode:
    key: str
    deps: tuple[str, ...]
    title: str
    objective: str


@dataclass(frozen=True)
class BoundedDagPlan:
    reason: str
    tasks: tuple[BoundedDagNode, ...] = field(default_factory=tuple)
    error: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    premium_requests: float = 0.0


def _prompt(objective: str) -> str:
    from ..roles.prompts.planner import build_bounded_dag_prompt

    return build_bounded_dag_prompt(objective)


def _extract(result: Any) -> str:
    messages = list(getattr(result, "agent_messages", None) or [])
    if messages:
        return str(messages[-1] or "").strip()
    return str(getattr(result, "last_agent_message", "") or "").strip()


_PLAN_LINE = re.compile(
    r"^(?P<key>PLAN_REASON|TASK_KEY|TASK_DEPS|TASK_TITLE|TASK_OBJECTIVE)"
    r"\s*[:=]\s*(?P<value>.*)$",
    re.IGNORECASE,
)


def _parse_key_value_plan(text: str) -> dict[str, Any]:
    reason = ""
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field_map = {
        "TASK_KEY": "key",
        "TASK_TITLE": "title",
        "TASK_OBJECTIVE": "objective",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("`").strip()
        match = _PLAN_LINE.match(line)
        if match is None:
            continue
        key = match.group("key").upper()
        value = match.group("value").strip()
        if key == "PLAN_REASON":
            reason = value
            continue
        if key == "TASK_KEY":
            if current is not None:
                tasks.append(current)
            current = {"key": value, "deps": []}
            continue
        if current is None:
            raise ValueError(f"{key} appeared before TASK_KEY")
        if key == "TASK_DEPS":
            current["deps"] = [dep.strip() for dep in value.split(",") if dep.strip()]
        else:
            current[field_map[key]] = value
    if current is not None:
        tasks.append(current)
    return {"reason": reason, "tasks": tasks}


def _validate(payload: object) -> tuple[str, tuple[BoundedDagNode, ...]]:
    if not isinstance(payload, dict):
        raise ValueError("planner output is not an object")
    reason = str(payload.get("reason") or "").strip()
    rows = payload.get("tasks")
    if not reason or not isinstance(rows, list) or not rows:
        raise ValueError("planner output has no bounded task batch")
    nodes: list[BoundedDagNode] = []
    keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("planner task is not an object")
        key = str(row.get("key") or "").strip()
        title = str(row.get("title") or "").strip()
        objective = str(row.get("objective") or "").strip()
        raw_deps = row.get("deps")
        if not key or key in keys or not title or not objective or not isinstance(raw_deps, list):
            raise ValueError("planner task fields are invalid or duplicate")
        deps = tuple(dict.fromkeys(str(dep).strip() for dep in raw_deps if str(dep).strip()))
        if key in deps:
            raise ValueError(f"planner task {key!r} depends on itself")
        keys.add(key)
        nodes.append(BoundedDagNode(key=key, deps=deps, title=title, objective=objective))
    for node in nodes:
        unknown = [dep for dep in node.deps if dep not in keys]
        if unknown:
            raise ValueError(f"planner task {node.key!r} has unknown deps: {unknown}")
    remaining = {node.key: set(node.deps) for node in nodes}
    done: set[str] = set()
    while remaining:
        ready = [key for key, deps in remaining.items() if deps <= done]
        if not ready:
            raise ValueError("planner task graph contains a cycle")
        for key in ready:
            done.add(key)
            remaining.pop(key)
    return reason, tuple(nodes)


def plan_bounded_dag(
    runner: Any,
    objective: str,
    *,
    workdir: Path | str,
    model: str | None = None,
    reasoning_effort: str = "high",
) -> BoundedDagPlan:
    try:
        result = gateway_run_exec(
            runner,
            prompt=_prompt(objective),
            resume_thread_id=None,
            options=RunnerOptions(
                model=model,
                reasoning_effort=reasoning_effort,
                working_dir=str(Path(workdir).expanduser().resolve()),
                sandbox_mode="read-only",
                skip_git_repo_check=True,
            ),
            run_label="planner.bounded_dag",
        )
    except Exception as exc:  # noqa: BLE001
        return BoundedDagPlan(reason="planner failed", error=f"{type(exc).__name__}: {exc}")
    usage = {
        "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
        "cached_input_tokens": int(getattr(result, "cached_input_tokens", 0) or 0),
        "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
        "reasoning_output_tokens": int(getattr(result, "reasoning_output_tokens", 0) or 0),
        "premium_requests": float(getattr(result, "premium_requests", 0.0) or 0.0),
    }
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return BoundedDagPlan(
            reason="planner failed",
            error=str(getattr(result, "fatal_error", "") or "planner exited non-zero"),
            **usage,
        )
    try:
        payload = _parse_key_value_plan(_extract(result))
        reason, tasks = _validate(payload)
    except (TypeError, ValueError) as exc:
        return BoundedDagPlan(
            reason="planner output invalid",
            error=f"{type(exc).__name__}: {exc}",
            **usage,
        )
    return BoundedDagPlan(reason=reason, tasks=tasks, **usage)


__all__ = [
    "BoundedDagNode",
    "BoundedDagPlan",
    "plan_bounded_dag",
]
