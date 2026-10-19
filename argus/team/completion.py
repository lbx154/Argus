"""Publish one durable Manager-facing summary per completed Team generation."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from ..core.operator_messages import publish_operator_message
from . import _store, leaderboard, roster, task_board

log = logging.getLogger(__name__)
_STATE_FILE = "completion_summary.json"
_TERMINAL_STATES = frozenset({"done", "failed", "blocked"})
# A delivery that failed waits 1 minute, doubling to an hour, before the next
# try; the Curator ticks every few seconds. Both the wait and the summary are
# kept in this process: a disk that cannot take the message cannot take them.
_DELIVERY_RETRY_BASE_SECONDS = 60.0
_DELIVERY_RETRY_CAP_SECONDS = 3600.0
_DELIVERY_BACKOFF: dict[str, tuple[int, float]] = {}
_SUMMARIES: dict[str, str] = {}


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


def _generation_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "team_id": payload["team_id"],
            "generation": payload["generation"],
            "tasks": [
                {
                    "task_id": row["task_id"],
                    "state": row["state"],
                    "pending_question": row["pending_question"],
                    "result_shard": row["result_shard"],
                }
                for row in payload["tasks"]
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    fingerprint = _generation_fingerprint(payload)
    state_path = root / _STATE_FILE
    prior = _store.read_json(state_path, default={})
    same = isinstance(prior, dict) and prior.get("fingerprint") == fingerprint
    if same and prior.get("delivered"):
        return False
    key = f"{state_path}:{fingerprint}"
    failures, retry_at = _DELIVERY_BACKOFF.get(key, (0, 0.0))
    if time.time() < retry_at:
        return False
    # A summary written for this generation is kept, so a publish that failed
    # is retried without asking the Manager again.
    summary = (str(prior.get("summary") or "") if same else "") or _SUMMARIES.get(key, "")
    if not summary and summarize is not None:
        try:
            summary = str(summarize(_prompt(payload)) or "").strip()
        except Exception:  # noqa: BLE001 - deterministic fallback must still notify
            log.exception("team completion Manager summary failed for %s", root)
    if not summary:
        summary = _fallback(payload)
    _SUMMARIES[key] = summary
    try:
        if not same or not prior.get("summary"):
            _store.atomic_write_json(state_path, {
                "fingerprint": fingerprint,
                "generation": payload["generation"],
                "team_id": payload["team_id"],
                "summary": summary,
                "delivered": False,
            })
        message_id = f"team-summary-{fingerprint[:16]}"
        publish_operator_message(
            conversation_root,
            text=summary,
            message_id=message_id,
            event_fields={
            "team_id": payload["team_id"],
            "team_completion": True,
            },
        )
        done = sum(row["state"] == "done" for row in payload["tasks"])
        failed = sum(row["state"] == "failed" for row in payload["tasks"])
        blocked = sum(row["state"] == "blocked" for row in payload["tasks"])
        _store.atomic_write_json(state_path, {
            "fingerprint": fingerprint,
            "generation": payload["generation"],
            "team_id": payload["team_id"],
            "done": done,
            "failed": failed,
            "blocked": blocked,
            "summary": summary,
            "delivered": True,
        })
    except Exception:
        _DELIVERY_BACKOFF[key] = (failures + 1, time.time() + min(
            _DELIVERY_RETRY_CAP_SECONDS, _DELIVERY_RETRY_BASE_SECONDS * 2 ** min(failures, 10),
        ))
        raise
    _DELIVERY_BACKOFF.pop(key, None)
    _SUMMARIES.pop(key, None)
    return True
