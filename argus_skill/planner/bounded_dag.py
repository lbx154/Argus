"""Compact Planner pass for Manager-authored bounded tasks."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec

SCHEMA_PATH = str(Path(__file__).with_name("bounded_dag_schema.json"))


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
    return (
        "You are the bounded-task Planner. Decompose the Manager handoff into a "
        "small executable backlog DAG; do not solve the task and do not create files.\n\n"
        "Rules:\n"
        "- Every node gets one fresh Engineer session. The Engineer decides from "
        "the completed work and verification whether an independent Reviewer is "
        "useful; framework-required gates may still force review. Minimize total "
        "cost: default to ONE cohesive node for one code/deliverable change, and "
        "use 2-4 only for genuinely independent artifacts or hard dependencies.\n"
        "- Each node must fit one fresh Engineer session and, when the Engineer "
        "requests it or the framework requires it, one Reviewer plus at most a "
        "small Reviewer-requested repair budget.\n"
        "- Fold prerequisite reading/audit, implementation, its tests, concise "
        "documentation, and final verification into the SAME node whenever one "
        "Engineer can do them coherently.\n"
        "- Never create standalone inspect/audit/planning or final-test/verification "
        "nodes when an implementation node can perform those checks itself.\n"
        "- Each downstream node must own a distinct durable deliverable that an "
        "upstream node is unlikely to satisfy incidentally; avoid overlapping or "
        "repeat-verification objectives.\n"
        "- Every objective must name exact files it reads/writes and one decisive "
        "acceptance command or check. A dependent node explicitly reads upstream "
        "artifacts.\n"
        "- Nodes execute directly. Do not assign planning/spec/brief creation unless "
        "that document is itself the requested deliverable. Do not initialize Git, "
        "create worktrees/branches, commit, spawn subagents, or invoke meta-workflow "
        "playbooks.\n"
        "- Use unique key values and same-batch prerequisite keys in deps. The graph "
        "must be acyclic.\n"
        "- Preserve the operator's acceptance requirements across the DAG; do not add "
        "unrelated research or ceremony.\n"
        "- Return JSON only matching the supplied schema.\n\n"
        "Manager execution handoff:\n"
        + objective.strip()
    )


def _extract(result: Any) -> str:
    messages = list(getattr(result, "agent_messages", None) or [])
    if messages:
        return str(messages[-1] or "").strip()
    return str(getattr(result, "last_agent_message", "") or "").strip()


def _validate(payload: object) -> tuple[str, tuple[BoundedDagNode, ...]]:
    if not isinstance(payload, dict):
        raise ValueError("planner output is not an object")
    reason = str(payload.get("reason") or "").strip()
    rows = payload.get("tasks")
    if not reason or not isinstance(rows, list) or not 1 <= len(rows) <= 4:
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
        if (
            not key
            or key in keys
            or not title
            or not objective
            or not isinstance(raw_deps, list)
        ):
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
                output_schema_path=SCHEMA_PATH,
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
        "reasoning_output_tokens": int(
            getattr(result, "reasoning_output_tokens", 0) or 0
        ),
        "premium_requests": float(getattr(result, "premium_requests", 0.0) or 0.0),
    }
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return BoundedDagPlan(
            reason="planner failed",
            error=str(getattr(result, "fatal_error", "") or "planner exited non-zero"),
            **usage,
        )
    try:
        payload = json.loads(_extract(result))
        reason, tasks = _validate(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return BoundedDagPlan(
            reason="planner output invalid",
            error=f"{type(exc).__name__}: {exc}",
            **usage,
        )
    return BoundedDagPlan(reason=reason, tasks=tasks, **usage)


__all__ = [
    "BoundedDagNode",
    "BoundedDagPlan",
    "SCHEMA_PATH",
    "plan_bounded_dag",
]
