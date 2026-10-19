"""Publish one durable Manager-facing summary per completed Team generation."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from ..core.operator_messages import publish_operator_message
from . import _store, leaderboard, roster, task_board

log = logging.getLogger(__name__)
_STATE_FILE = "completion_summary.json"
_TERMINAL_STATES = frozenset({"done", "failed", "blocked"})


def _payload(root: Path, marker: dict[str, Any]) -> dict[str, Any] | None:
    tasks = sorted(task_board.snapshot(root), key=lambda row: str(row.get("task_id") or ""))
    if not tasks:
        return None
    if any(str(row.get("state") or "") in {"claimed", "running"} for row in tasks):
        return None
    done_ids = {
        str(row.get("task_id") or "")
        for row in tasks
        if str(row.get("state") or "") == "done"
    }
    pending = [row for row in tasks if str(row.get("state") or "") == "pending"]
    if any(all(str(dep) in done_ids for dep in row.get("deps") or []) for row in pending):
        return None
    normalized_tasks = []
    for row in tasks:
        state = str(row.get("state") or "")
        reason = str(row.get("reason") or "")
        if state == "pending":
            state = "blocked"
            reason = reason or "dependency chain cannot proceed"
        elif state not in _TERMINAL_STATES:
            return None
        normalized_tasks.append({
            "task_id": str(row.get("task_id") or ""),
            "title": str(row.get("title") or row.get("task_id") or ""),
            "state": state,
            "reason": reason,
            "target": str(row.get("target") or ""),
            "result_shard": str(row.get("result_shard") or ""),
            "owner": str(row.get("owner") or ""),
            "pending_question": str(row.get("pending_question") or ""),
        })
    manifest = roster.load(root)
    return {
        "team_id": str(marker.get("team_id") or manifest.get("team_id") or root.name),
        "generation": float(marker.get("created_ts") or 0.0),
        "mission": str(manifest.get("mission_objective") or ""),
        "tasks": normalized_tasks,
        "leaderboard": leaderboard.read(root),
    }


def _outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """Which generation ended, and how: a changed outcome is published again."""
    return {
        "team_id": payload["team_id"],
        "generation": payload["generation"],
        **{
            state: sum(row["state"] == state for row in payload["tasks"])
            for state in ("done", "failed", "blocked")
        },
    }


def _prompt(payload: dict[str, Any]) -> str:
    facts = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "You are the Argus Manager. A parallel Team campaign has just become quiescent: "
        "every teammate process exited and every task is done, failed, or blocked. "
        "Write the final "
        "operator-facing chat summary in the mission's language. Use 5-10 concise lines. "
        "State completed/failed/blocked counts, the strongest result or mechanism, important "
        "artifacts, honest failures, and the most useful next step. Do not expose internal "
        "state paths unless they are result artifacts. Do not claim success for failed tasks.\n\n"
        f"TEAM FACTS:\n{facts}"
    )


def _fallback(payload: dict[str, Any]) -> str:
    tasks = payload["tasks"]
    done = [row for row in tasks if row["state"] == "done"]
    failed = [row for row in tasks if row["state"] == "failed"]
    operator_waits = [
        row for row in tasks if row["state"] == "blocked" and row["pending_question"]
    ]
    blocked = [
        row for row in tasks if row["state"] == "blocked" and not row["pending_question"]
    ]
    lines = [
        "Team completed"
        f" · {len(done)} done"
        f" · {len(failed)} failed"
        f" · {len(operator_waits) + len(blocked)} blocked.",
    ]
    if done:
        lines.append("Completed: " + "; ".join(row["title"] for row in done[:6]))
    if failed:
        def safe_reason(reason: str) -> str:
            if "working dir vanished" in reason or "working directory" in reason:
                return "working directory unavailable"
            return re.sub(r"(?<!\w)/(?:[^\s,;:)]+/?)+", "[internal path]", reason)

        lines.append(
            "Failed: " + "; ".join(
                f"{row['title']} ({safe_reason(row['reason']) if row['reason'] else 'no reason recorded'})"
                for row in failed[:4]
            )
        )
    if operator_waits:
        lines.append(
            "Waiting for operator: "
            + "; ".join(
                f"{row['title']} ({row['pending_question']})"
                for row in operator_waits[:4]
            )
        )
    if blocked:
        lines.append("Blocked: " + "; ".join(row["title"] for row in blocked[:4]))
    board = payload.get("leaderboard") or {}
    best = [
        f"{target}: {entry['best'].get('mechanism') or '(unnamed)'}={entry['best'].get('metric')}"
        for target, entry in sorted(board.items())
        if isinstance(entry, dict) and isinstance(entry.get("best"), dict)
    ]
    if best:
        lines.append("Best recorded: " + "; ".join(best[:4]))
    lines.append("The detailed task records and result shards remain available in the Team artifacts.")
    return "\n".join(lines)


def publish_if_complete(
    root: Path,
    *,
    marker: dict[str, Any],
    conversation_root: Path | None,
    summarize: Callable[[str], str] | None,
) -> bool:
    """Publish exactly once for the current marker generation when all tasks terminate."""
    if conversation_root is None:
        return False
    root = Path(root)
    payload = _payload(root, marker)
    if payload is None:
        return False
    outcome = _outcome(payload)
    state_path = root / _STATE_FILE
    prior = _store.read_json(state_path, default={})
    if (
        isinstance(prior, dict)
        and prior.get("delivered")
        and all(prior.get(key) == value for key, value in outcome.items())
    ):
        return False
    summary = ""
    if summarize is not None:
        try:
            summary = str(summarize(_prompt(payload)) or "").strip()
        except Exception:  # noqa: BLE001 - deterministic fallback must still notify
            log.exception("team completion Manager summary failed for %s", root)
    if not summary:
        summary = _fallback(payload)
    # The same outcome keeps its id, so a retry after a crash is not a second message.
    message_id = "team-summary-" + "-".join(str(value) for value in outcome.values())
    publish_operator_message(
        conversation_root,
        text=summary,
        message_id=message_id,
        event_fields={
        "team_id": payload["team_id"],
        "team_completion": True,
        },
    )
    _store.atomic_write_json(state_path, {**outcome, "summary": summary, "delivered": True})
    return True
