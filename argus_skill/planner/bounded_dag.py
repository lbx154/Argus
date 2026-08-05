"""Compact Planner pass for Manager-authored bounded tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from .planner import (
    TASK_SCOPE_BOUNDED,
    hydrate_task_context_refs,
    parse_task_context_refs,
)


@dataclass(frozen=True)
class BoundedDagNode:
    key: str
    deps: tuple[str, ...]
    title: str
    objective: str
    acceptance_check: str = ""
    non_goals: tuple[str, ...] = ()
    context_refs: tuple[dict[str, str], ...] = ()
    scope: str = TASK_SCOPE_BOUNDED
    stage_closing: bool = False
    require_independent_review: bool = False
    skip_stage_transition: bool = False


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
    r"TASK_ACCEPTANCE_CHECK|TASK_NON_GOALS|TASK_CONTEXT_REFS|TASK_SCOPE|"
    r"TASK_STAGE_CLOSING|TASK_REQUIRE_INDEPENDENT_REVIEW|"
    r"TASK_SKIP_STAGE_TRANSITION)"
    r"\s*[:=]\s*(?P<value>.*)$",
    re.IGNORECASE,
)


def _parse_task_boolean(raw: str, field: str) -> bool:
    normalized = str(raw or "").strip().casefold()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ValueError(f"{field} must be explicitly true or false")


def _parse_key_value_plan(text: str) -> dict[str, Any]:
    reason = ""
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field_map = {
        "TASK_KEY": "key",
        "TASK_TITLE": "title",
        "TASK_OBJECTIVE": "objective",
        "TASK_ACCEPTANCE_CHECK": "acceptance_check",
        "TASK_SCOPE": "scope",
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
        elif key == "TASK_NON_GOALS":
            current["non_goals"] = [
                item.strip() for item in value.split("|") if item.strip()
            ]
        elif key == "TASK_CONTEXT_REFS":
            current["context_refs"] = parse_task_context_refs(value)
        elif key == "TASK_STAGE_CLOSING":
            current["stage_closing"] = _parse_task_boolean(
                value, "TASK_STAGE_CLOSING"
            )
        elif key == "TASK_REQUIRE_INDEPENDENT_REVIEW":
            current["require_independent_review"] = _parse_task_boolean(
                value, "TASK_REQUIRE_INDEPENDENT_REVIEW"
            )
        elif key == "TASK_SKIP_STAGE_TRANSITION":
            current["skip_stage_transition"] = _parse_task_boolean(
                value, "TASK_SKIP_STAGE_TRANSITION"
            )
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
        required_controls = (
            "scope",
            "stage_closing",
            "require_independent_review",
            "skip_stage_transition",
        )
        missing_controls = [field for field in required_controls if field not in row]
        if missing_controls:
            raise ValueError(
                "planner task is missing required control fields: "
                + ", ".join(missing_controls)
            )
        key = str(row.get("key") or "").strip()
        title = str(row.get("title") or "").strip()
        objective = str(row.get("objective") or "").strip()
        raw_deps = row.get("deps")
        scope = str(row.get("scope") or TASK_SCOPE_BOUNDED).strip()
        if not key or key in keys or not title or not objective or not isinstance(raw_deps, list):
            raise ValueError("planner task fields are invalid or duplicate")
        if scope != TASK_SCOPE_BOUNDED:
            raise ValueError("bounded Planner task scope must be bounded")
        stage_closing = bool(row.get("stage_closing", False))
        require_independent_review = bool(
            row.get("require_independent_review", False)
        )
        skip_stage_transition = bool(row.get("skip_stage_transition", False))
        if skip_stage_transition and (
            stage_closing or not require_independent_review
        ):
            raise ValueError(
                "skip_stage_transition requires independent review and "
                "stage_closing=false"
            )
        deps = tuple(dict.fromkeys(str(dep).strip() for dep in raw_deps if str(dep).strip()))
        if key in deps:
            raise ValueError(f"planner task {key!r} depends on itself")
        keys.add(key)
        nodes.append(
            BoundedDagNode(
                key=key,
                deps=deps,
                title=title,
                objective=objective,
                acceptance_check=str(row.get("acceptance_check") or "").strip(),
                non_goals=tuple(
                    str(item).strip()
                    for item in (row.get("non_goals") or [])
                    if str(item).strip()
                ),
                context_refs=tuple(
                    {str(field): str(value) for field, value in ref.items()}
                    for ref in (row.get("context_refs") or [])
                    if isinstance(ref, dict)
                ),
                scope=scope,
                stage_closing=stage_closing,
                require_independent_review=require_independent_review,
                skip_stage_transition=skip_stage_transition,
            )
        )
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
        output = _extract(result)
        try:
            payload = _parse_key_value_plan(output)
            reason, tasks = _validate(payload)
            # Context refs are advisory, but malformed/escaping paths are a
            # security boundary. Validate them while the Planner's one repair
            # attempt is still available instead of discovering the defect only
            # after Manager has accepted an otherwise-executable plan.
            for task in tasks:
                hydrate_task_context_refs(list(task.context_refs), workdir)
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
