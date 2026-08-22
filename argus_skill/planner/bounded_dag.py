"""Compact Planner pass for Manager-authored bounded tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.portable_filename import normalized_logical_identifier
from ..core.role_decision import latest_role_decision
from ..core.run_gateway import run_exec as gateway_run_exec


@dataclass(frozen=True)
class BoundedDagNode:
    key: str
    deps: tuple[str, ...]
    title: str
    objective: str
    acceptance_check: str = ""
    non_goals: tuple[str, ...] = ()
    vertical: str = ""
    require_independent_review: bool = False


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
    r"^(?P<key>PLAN_REASON|TASK_KEY|TASK_DEPS|TASK_TITLE|TASK_OBJECTIVE|"
    r"TASK_ACCEPTANCE_CHECK|TASK_NON_GOALS|TASK_VERTICAL|"
    r"TASK_REQUIRE_INDEPENDENT_REVIEW)"
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
        "TASK_ACCEPTANCE_CHECK": "acceptance_check",
        "TASK_VERTICAL": "vertical",
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
            current["deps"] = [
                dep.strip() for dep in value.split(",") if dep.strip()
            ]
        elif key == "TASK_NON_GOALS":
            current["non_goals"] = [
                item.strip() for item in value.split("|") if item.strip()
            ]
        elif key == "TASK_REQUIRE_INDEPENDENT_REVIEW":
            current["require_independent_review"] = value
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
    identity_to_key: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("planner task is not an object")
        key = str(row.get("key") or "").strip()
        title = str(row.get("title") or "").strip()
        objective = str(row.get("objective") or "").strip()
        raw_deps = row.get("deps")
        if not key or not title or not objective or not isinstance(raw_deps, list):
            raise ValueError("planner task fields are invalid or duplicate")
        key_identity = normalized_logical_identifier(key)
        if not key_identity:
            raise ValueError("planner task fields are invalid or duplicate")
        if key_identity in identity_to_key:
            raise ValueError("planner task fields are invalid or duplicate")
        deps: list[str] = []
        seen_dep_identities: set[str] = set()
        for raw_dep in raw_deps:
            dep = str(raw_dep).strip()
            if not dep:
                continue
            dep_identity = normalized_logical_identifier(dep)
            if not dep_identity or dep_identity in seen_dep_identities:
                continue
            seen_dep_identities.add(dep_identity)
            deps.append(dep)
        if key_identity in seen_dep_identities:
            raise ValueError(f"planner task {key!r} depends on itself")
        raw_non_goals = row.get("non_goals") or []
        if isinstance(raw_non_goals, str):
            non_goals = (raw_non_goals.strip(),) if raw_non_goals.strip() else ()
        elif isinstance(raw_non_goals, list):
            non_goals = tuple(
                str(item).strip()
                for item in raw_non_goals
                if str(item).strip()
            )
        else:
            raise ValueError("planner task non_goals must be text or an array")
        raw_review = row.get("require_independent_review", False)
        if isinstance(raw_review, bool):
            require_independent_review = raw_review
        elif str(raw_review).strip().casefold() in {"true", "false"}:
            require_independent_review = (
                str(raw_review).strip().casefold() == "true"
            )
        else:
            raise ValueError(
                "planner task require_independent_review must be true or false"
            )
        identity_to_key[key_identity] = key
        nodes.append(
            BoundedDagNode(
                key=key,
                deps=tuple(deps),
                title=title,
                objective=objective,
                acceptance_check=str(row.get("acceptance_check") or "").strip(),
                non_goals=non_goals,
                vertical=str(row.get("vertical") or "").strip(),
                require_independent_review=require_independent_review,
            )
        )
    nodes = [
        replace(
            node,
            deps=tuple(
                identity_to_key[dep_identity]
                for dep in node.deps
                if (dep_identity := normalized_logical_identifier(dep)) in identity_to_key
            ),
        )
        for node in nodes
    ]
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
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "premium_requests": 0.0,
    }
    prompt = _prompt(objective)
    for attempt in range(2):
        try:
            result = gateway_run_exec(
                runner,
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=model,
                    reasoning_effort=reasoning_effort,
                    working_dir=str(Path(workdir).expanduser().resolve()),
                    dangerous_yolo=True,
                    skip_git_repo_check=True,
                ),
                run_label=(
                    "planner.bounded_dag"
                    if attempt == 0
                    else "planner.bounded_dag.repair"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return BoundedDagPlan(
                reason="planner failed",
                error=f"{type(exc).__name__}: {exc}",
                **usage,
            )
        for usage_field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            usage[usage_field] = int(usage[usage_field]) + int(
                getattr(result, usage_field, 0) or 0
            )
        usage["premium_requests"] = float(usage["premium_requests"]) + float(
            getattr(result, "premium_requests", 0.0) or 0.0
        )
        if int(getattr(result, "exit_code", 0) or 0) != 0:
            return BoundedDagPlan(
                reason="planner failed",
                error=str(
                    getattr(result, "fatal_error", "") or "planner exited non-zero"
                ),
                **usage,
            )
        process_decision = latest_role_decision(result, "planner")
        output = _extract(result)
        try:
            payload = (
                process_decision
                if process_decision is not None
                else _parse_key_value_plan(output)
            )
            reason, tasks = _validate(payload)
            return BoundedDagPlan(reason=reason, tasks=tasks, **usage)
        except (TypeError, ValueError) as exc:
            validation_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                from ..roles.prompts.planner import build_bounded_dag_repair_prompt

                prompt = build_bounded_dag_repair_prompt(
                    objective,
                    output,
                    validation_error,
                )
                continue
            return BoundedDagPlan(
                reason="planner output invalid",
                error=validation_error,
                **usage,
            )
    raise AssertionError("bounded DAG repair loop exhausted unexpectedly")


__all__ = [
    "BoundedDagNode",
    "BoundedDagPlan",
    "plan_bounded_dag",
]
