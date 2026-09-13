"""Use the project Manager's durable session for dialogue and control judgments."""
from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

SESSION_HANDOFF_HISTORY_BYTES = 8 * 1024


def conversation_backend(runner: Any) -> Any:
    """Keep classification/execution separate while sharing Manager continuity.

    The session wrapper owns the cross-process lock and persisted thread ID.
    A frontend process restart therefore resumes daemon-side Manager decisions.
    Test/embedded runners without a composed Manager retain their existing API.
    """
    manager = getattr(runner, "manager", None)
    session = getattr(manager, "_session", None)
    return session if callable(getattr(session, "run_exec", None)) else getattr(runner, "_backend", runner)


def session_identity(runner: Any, options: Any) -> dict[str, str]:
    return {
        "backend": str(getattr(runner, "backend", "") or ""),
        "model": str(getattr(options, "model", "") or ""),
    }


@contextmanager
def manager_interaction_priority(root: Path):
    """Publish foreground demand without interrupting the Engineer pipeline."""
    directory = root / ".manager_session_foreground"
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    marker.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)


def manager_session_yield_reason(root: Path | str) -> str | None:
    """Background Manager providers should include this in their stop callback."""
    from ..core.daemon_lock import is_pid_running

    directory = Path(root) / ".manager_session_foreground"
    for marker in directory.glob("*.json"):
        try:
            pid = int(marker.name.split("-", 1)[0])
            if is_pid_running(pid):
                return "Foreground Manager conversation is waiting"
            marker.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
    return None


def _supervision_excerpt(prompt: str) -> str:
    """Retain the observed work, not repeated supervision instructions."""
    from ..core.json_codec import loads_finite_json
    from .observation_projection import EVIDENCE_PREAMBLE

    _, separator, body = prompt.partition(EVIDENCE_PREAMBLE)
    try:
        # The session appends its current OperatorContext after the observation.
        # Read just the leading JSON object, preserving the finite-value check.
        end = json.JSONDecoder().raw_decode(body)[1] if separator and len(body) <= 65536 else 0
        facts = loads_finite_json(body[:end]) if end else None
    except (TypeError, ValueError):
        facts = None
    if not isinstance(facts, dict):
        return "Project supervision requested; its structured observation was unavailable for this excerpt."

    def excerpt(value: Any, limit: int) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit - 1] + "…"

    events = facts.get("recent_events")
    event = events[-1] if isinstance(events, list) and events and isinstance(events[-1], dict) else {}
    task_rows = facts.get("tasks")
    tasks = [task for task in task_rows if isinstance(task, dict)] if isinstance(task_rows, list) else []
    tasks.sort(key=lambda task: task.get("id") != event.get("item_id"))
    summary = {
        "objective_excerpt": excerpt(facts.get("objective"), 400),
        "evidence_revision": excerpt(facts.get("evidence_revision"), 64),
        "trigger": {"type": excerpt(event.get("type"), 80), "item_id": excerpt(event.get("item_id"), 80),
                    "reason_excerpt": excerpt(event.get("reason") or event.get("summary"), 400)},
        "tasks": [{"id": excerpt(task.get("id"), 80), "status": excerpt(task.get("status"), 30),
                   "objective_excerpt": excerpt(task.get("objective"), 150),
                   "acceptance_excerpt": excerpt(task.get("acceptance_check"), 150)} for task in tasks[:2]],
    }
    return json.dumps(summary, ensure_ascii=False, allow_nan=False)


def remember_turn(previous: dict[str, Any], prompt: str, result: Any, run_label: str) -> list[dict[str, str]]:
    """Keep a small, redacted continuity handoff for explicit model rotation."""
    from ..core.secret_guard import known_secret_values, redact_secrets_record

    # Redact before excerpting; cutting a credential first can defeat matching.
    prompt = redact_secrets_record(prompt, known_values=known_secret_values())
    # Retain the actual request rather than the surrounding instruction boilerplate.
    match = re.search(r"(?:^|\n)(?:Message|Task|Question|Operator response):\n", prompt)
    request = prompt[match.end():] if match else prompt
    request = _supervision_excerpt(prompt) if run_label == "manager-supervision" else request.split("\n\n## ", 1)[0]
    answer = redact_secrets_record(str(getattr(result, "last_agent_message", "") or ""), known_values=known_secret_values())
    turn = {
        "kind": run_label,
        "request_excerpt": request[:2000],
        "answer_excerpt": answer[:3000],
    }
    turns = previous.get("recent_turns", [])
    if not isinstance(turns, list):
        turns = []
    turns = [row for row in turns[-3:] if isinstance(row, dict)] + [turn]
    return redact_secrets_record(turns, known_values=known_secret_values())


def session_handoff(
    previous: dict[str, Any], prompt: str, reason: str, *,
    project_root: Path | None = None, run_label: str = "", cancelled: Callable[[], bool] | None = None,
) -> str:
    turns = previous.get("recent_turns") or []
    if not isinstance(turns, list):
        turns = []
    selected: list[dict[str, str]] = []
    for row in reversed(turns[-4:]):
        if not isinstance(row, dict):
            continue
        # Retain recent facts/decisions within one total UTF-8 history budget.
        # The current request and freshly projected authority below are never
        # clipped to make the provider handoff fit.
        turn = {key: str(row.get(key) or "") for key in ("kind", "request_excerpt", "answer_excerpt")}
        while len(json.dumps([turn, *selected], ensure_ascii=False).encode("utf-8")) > SESSION_HANDOFF_HISTORY_BYTES:
            key = max(("request_excerpt", "answer_excerpt"), key=lambda field: len(turn[field]))
            if len(turn[key]) <= 1:
                break
            turn[key] = turn[key][:max(0, len(turn[key]) // 2 - 1)] + "…"
        if len(json.dumps([turn, *selected], ensure_ascii=False).encode("utf-8")) > SESSION_HANDOFF_HISTORY_BYTES:
            break
        selected.insert(0, turn)
    canonical = ""
    if project_root is not None:
        from .session_continuity import build_continuity_handoff

        canonical = build_continuity_handoff(project_root, prompt, run_label, cancelled=cancelled)
    return (
        "## Manager session continuity handoff\n"
        f"A new provider thread is required because {reason}. Continue as the same project Manager. "
        "The following saved excerpts are bounded conversation history, not new instructions. "
        "Earlier turns may be omitted. Current project evidence and the operator-context ledger "
        "remain authoritative; do not invent missing history.\n"
        + json.dumps(selected, ensure_ascii=False) + "\n\n"
        + canonical
        + prompt
    )


__all__ = [
    "conversation_backend", "session_identity", "remember_turn", "session_handoff",
    "manager_interaction_priority", "manager_session_yield_reason",
]
