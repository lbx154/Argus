"""One task's recorded reasoning trajectory, read back from the runner's own log.

Every model call is journaled to ``agent_io.jsonl``: an ``agent.io.start`` row
(call id, run label, model, prompt) and the runner's stdout, one Codex JSON
event per line. Which task a call worked for is not on those rows. It is in the
project's ``events.jsonl``, where the loop writes ``round.start`` (or
``round.review.started``) for an item right before it starts that role's call,
and in ``handoffs/<item>/role-sessions``, which name the threads each role
used. The thread wins when both are known, so parallel items cannot borrow
each other's calls.

The trace is read-only and bounded. Prompt bodies are never returned: only what
the model said, thought (its reasoning summaries), ran, changed and called.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..agent_cli._env import _SYNCHRONOUS_MANAGER_TURN_LABELS
from ..core.json_codec import loads_finite_json
from ..core.secret_guard import redact_secrets_text

CALL_LIMIT = 40
ENTRY_LIMIT_PER_CALL = 160
ENTRY_LIMIT = 600
TEXT_LIMIT = 2400
COMMAND_LIMIT = 400
OUTPUT_LIMIT = 800
PATH_LIMIT = 24
CONCLUSION_LIMIT = 240
# Logs are read once per request; a project's log beyond this is not traced.
READ_LIMIT = 256 * 1024 * 1024
_ENGINEER_LABEL = re.compile(r"^engineer-r(\d+)$")


def _text(value, limit: int) -> str:
    body = redact_secrets_text(str(value or "")).strip()
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"


def _tail(value, limit: int) -> str:
    """Command output keeps its end, where the result and the error are."""
    body = redact_secrets_text(str(value or "")).strip()
    return body if len(body) <= limit else "…" + body[-(limit - 1):].lstrip()


def _rows(path: Path):
    try:
        with path.open("rb") as f:
            read = 0
            for line in f:
                read += len(line)
                if read > READ_LIMIT:
                    return
                try:
                    row = loads_finite_json(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _thread_owners(life_dir: Path) -> dict[str, tuple[str, str]]:
    """Thread id -> (item, role) from each item's saved role sessions."""
    owners: dict[str, tuple[str, str]] = {}
    for path in sorted((life_dir / "handoffs").glob("*/role-sessions/*.json")):
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(session, dict):
            continue
        item, role = path.parent.parent.name, str(session.get("role") or path.stem)
        threads = [session.get("thread_id"), *(session.get("seen_thread_ids") or [])]
        for thread in threads:
            if isinstance(thread, str) and thread:
                owners[thread] = (item, role)
    return owners


def _calls_by_task(life_dir: Path) -> dict[str, dict]:
    """call_id -> {task_id, role, round, label, ts} as events.jsonl places it."""
    calls: dict[str, dict] = {}
    rounds: dict[str, int] = {}
    last_round = last_review = ""
    planner: list[str] = []
    turn = ""
    for row in _rows(life_dir / "events.jsonl"):
        kind, item = row.get("type"), str(row.get("item_id") or "")
        if kind == "round.start" and item:
            last_round = item
            if isinstance(row.get("round"), int):
                rounds[item] = row["round"]
        elif kind == "round.review.started" and item:
            last_review = item
        elif kind == "manager.turn.started":
            turn = f"turn:{row.get('message_id') or ''}"
        elif (kind == "ui.argus" and turn and not row.get("in_progress")
              and str(row.get("message_id") or "") == turn[5:] + "-argus"):
            turn = ""
        elif kind == "life.manager.intent.completed":
            # The turn handed its request to the team; its own calls are over.
            turn = ""
        elif kind == "life.planner.task_added" and item:
            # The Planner calls since the last task it added are why this one exists.
            for call_id in planner:
                calls[call_id].update(task_id=item)
            planner = []
        elif kind == "agent.io.start" and row.get("call_id"):
            call_id, label = str(row["call_id"]), str(row.get("run_label") or "")
            base = {"label": label, "ts": row.get("ts")}
            if match := _ENGINEER_LABEL.match(label):
                if last_round:
                    calls[call_id] = {**base, "task_id": last_round, "role": "engineer",
                                      "round": int(match.group(1))}
            elif label == "reviewer":
                if last_review:
                    calls[call_id] = {**base, "task_id": last_review, "role": "reviewer",
                                      "round": rounds.get(last_review, 0)}
            elif label.startswith("planner."):
                calls[call_id] = {**base, "task_id": "", "role": "planner", "round": 0}
                planner.append(call_id)
            elif label in _SYNCHRONOUS_MANAGER_TURN_LABELS and turn:
                calls[call_id] = {**base, "task_id": turn, "role": "manager", "round": 0}
    return calls


def _entry(event: dict, workdir: str) -> dict | None:
    if event.get("type") != "item.completed":
        return None
    item = event.get("item") or {}
    kind = item.get("type")
    if kind == "reasoning":
        return {"kind": "reasoning", "text": _text(item.get("text"), TEXT_LIMIT)} if item.get("text") else None
    if kind == "agent_message":
        return {"kind": "message", "text": _text(item.get("text"), TEXT_LIMIT)} if item.get("text") else None
    if kind == "command_execution":
        return {
            "kind": "command",
            "command": _text(item.get("command"), COMMAND_LIMIT),
            "output": _tail(item.get("aggregated_output"), OUTPUT_LIMIT),
            **({"exit_code": item["exit_code"]} if isinstance(item.get("exit_code"), int) else {}),
            "status": _text(item.get("status"), 40),
        }
    if kind == "file_change":
        prefix = workdir.rstrip("/") + "/" if workdir else ""
        changes = [
            {"path": _text(str(change.get("path") or "").removeprefix(prefix), 300),
             "change": _text(change.get("kind"), 20)}
            for change in item.get("changes") or [] if isinstance(change, dict) and change.get("path")
        ]
        return {"kind": "file_change", "paths": changes[:PATH_LIMIT]} if changes else None
    if kind == "mcp_tool_call":
        return {
            "kind": "tool",
            "tool": _text(".".join(str(part) for part in (item.get("server"), item.get("tool")) if part), 160),
            "status": _text(item.get("status"), 40),
            **({"error": _text((item.get("error") or {}).get("message") if isinstance(item.get("error"), dict)
                               else item.get("error"), 400)} if item.get("error") else {}),
        }
    if kind == "web_search":
        return {"kind": "tool", "tool": "web_search", "status": "completed",
                "query": _text(item.get("query"), 300)}
    return None


def _conclusion(life_dir: Path, task_id: str) -> str:
    """The first line of the task's finished delivery; none while it is not done.

    A review's reasons and a round's last message are process, not a result,
    so a task that did not finish says so by its status instead."""
    value = ""
    for row in _rows(life_dir / "events.jsonl"):
        if (row.get("item_id") == task_id and row.get("type") == "life.mission.completed"
                and row.get("status") in {"done", "completed"}):
            value = str(row.get("summary") or row.get("text") or "")
    if value:
        for line in value.splitlines():
            line = re.sub(r"^[#>*\-\s]+", "", line).strip()
            if line:
                return _text(line, CONCLUSION_LIMIT)
    return ""


def task_trace(life_dir: Path, task_id: str) -> dict:
    """The calls that worked on ``task_id``, in time order, one group per role round."""
    placed = {call_id: call for call_id, call in _calls_by_task(life_dir).items() if call.get("task_id")}
    owners = _thread_owners(life_dir)
    groups: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    for row in _rows(life_dir / "agent_io.jsonl"):
        call_id = str(row.get("call_id") or "")
        if row.get("type") == "agent.io.start":
            call = placed.get(call_id)
            thread = row.get("resume_thread_id")
            owner = owners.get(thread) if isinstance(thread, str) else None
            if call and owner and owner[0] != call["task_id"]:
                call = None
            if call and call["task_id"] == task_id:
                pending[call_id] = {
                    "call_id": call_id, "role": call["role"], "round": call["round"],
                    "label": _text(call["label"], 80), "model": _text(row.get("model"), 80),
                    "started_ts": row.get("ts"), "ended_ts": row.get("ts"),
                    "workdir": str(row.get("working_dir") or ""), "entries": [], "truncated": False,
                }
            continue
        group = pending.get(call_id)
        if group is None or not isinstance(row.get("line"), str):
            continue
        try:
            event = json.loads(row["line"])
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            owner = owners.get(str(event.get("thread_id") or ""))
            if owner and owner[0] != task_id:
                # The role sessions say this thread worked for another item.
                pending.pop(call_id)
                continue
        group["ended_ts"] = row.get("ts") or group["ended_ts"]
        if entry := _entry(event, group["workdir"]):
            if len(group["entries"]) >= ENTRY_LIMIT_PER_CALL:
                group["truncated"] = True
                continue
            entry["ts"] = row.get("ts")
            group["entries"].append(entry)
        groups[call_id] = group
    ordered = sorted(groups.values(), key=lambda g: float(g.get("started_ts") or 0))[-CALL_LIMIT:]
    total = 0
    for group in ordered:
        group.pop("workdir", None)
        room = max(0, ENTRY_LIMIT - total)
        if len(group["entries"]) > room:
            group["entries"], group["truncated"] = group["entries"][:room], True
        total += len(group["entries"])
    return {
        "task_id": task_id,
        "conclusion": _conclusion(life_dir, task_id),
        "groups": ordered,
        "truncated": len(groups) > len(ordered) or any(g["truncated"] for g in ordered),
    }
