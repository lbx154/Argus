"""One durable HTTP handoff receipt, based on queue and startup evidence.

Daemon readiness and a Backlog claim are distinct from task execution. Direct
bridge callers retain their queue event; HTTP callers defer that event until
startup finishes, then publish this receipt once on their chosen transport.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from ..core.operator_messages import uses_cjk

_ACK_TITLE_LIMIT = 80


def _dispatch_ack_text(
    result: dict[str, Any], daemon: Any, daemon_alive: bool, *, operator_text: str = "",
) -> str:
    raw_item = result.get("item")
    item = raw_item if isinstance(raw_item, dict) else {}
    title = " ".join(str(item.get("title") or item.get("objective") or "").split())
    if len(title) > _ACK_TITLE_LIMIT:
        title = title[: _ACK_TITLE_LIMIT - 1] + "…"
    zh = uses_cjk(operator_text or title)
    named = (f"：{title}" if zh else f": {title}") if title else ""
    dispatch_state = result.get("dispatch_state")
    saved = "目标已更新" if dispatch_state == "planner_pending" else "任务已保存"
    saved_en = "The objective is saved" if dispatch_state == "planner_pending" else "The task is saved"

    # The endpoint's fresh startup result outranks the bridge's earlier queue
    # observation. A daemon may disappear between those two observations.
    if isinstance(daemon, dict):
        if daemon.get("admission_required"):
            return (
                f"{saved}，正在等待空闲的执行槽位{named}"
                if zh else f"{saved_en}. Waiting for a free executor slot{named}"
            )
        if daemon.get("control_busy"):
            return (
                f"{saved}，执行控制正忙，尚未确认启动{named}"
                if zh else f"{saved_en}. Executor control is busy; startup is not confirmed{named}"
            )
        if int(daemon.get("rc", 0)) != 0:
            diagnostic = str(daemon.get("startup_diagnostic") or daemon.get("diagnostic")
                             or daemon.get("error") or "unknown error")
            daemon["diagnostic"] = diagnostic
            daemon["error"] = "The background worker could not start."
            return (
                f"{saved}，但后台执行者没能启动。可在启动详情查看原因。"
                if zh else f"{saved_en}, but the background worker could not start. See startup details."
            )
    if dispatch_state == "already_queued":
        status = str(item.get("status") or "pending")
        status_zh = {"pending": "排队中", "running": "已接手", "done": "已完成",
                     "paused_operator": "已暂停", "failed": "失败"}.get(status, "已保存")
        return (
            f"这件事已存在（{status_zh}），没有重复创建。" if zh
            else f"This request already exists ({status}); no duplicate task was created."
        )
    if dispatch_state == "queued_after_current" and daemon_alive:
        return f"已排在当前工作之后{named}" if zh else f"Queued after the current work{named}"
    if dispatch_state == "running" and daemon_alive:
        return f"执行者已接手{named}" if zh else f"The executor has claimed this task{named}"
    if dispatch_state == "planner_pending":
        return (
            "目标已更新，等待 Planner 安排下一步。" if zh
            else "Objective updated; waiting for the Planner to schedule the next step."
        )
    return (
        f"已加入队列，等待执行者接手{named}" if zh
        else f"Queued; waiting for the executor to pick it up{named}"
    )


def record_task_dispatch_ack(
    sid: str,
    result: dict[str, Any],
    *,
    global_root: Path | str | None = None,
    on_fragment: Any = None,
    operator_text: str = "",
    cancelled: Any = None,
) -> str:
    """Derive truthful acknowledgement text from the daemon-start outcome,
    persist it durably, publish it on the caller's live UI channel, and set
    ``result["reply"]``.

    Unlike chat turns, transcript write failures are surfaced through the same
    live UI channel and result payload as the acknowledgement.

    Called after ``start_project_daemon`` in both blocking and streaming
    endpoints.

    Cancellation fences slow preparation and live output. An append already
    completed is historical evidence of the committed queue handoff; cancellation
    does not delete it or roll back the task itself.
    """
    import uuid

    from .manager_pending_question import _emit_ui_turn

    def is_cancelled() -> bool:
        return callable(cancelled) and bool(cancelled())

    if is_cancelled():
        return ""

    daemon = result.get("daemon")
    daemon_alive = result.get("daemon_alive", False)

    # Derive truthful human-readable text, in the operator's language and
    # naming the task, so the acknowledgement reads as a sentence rather than
    # a status code.
    text = _dispatch_ack_text(result, daemon, daemon_alive, operator_text=operator_text)

    # Resolve life_dir
    root = Path(global_root) if global_root else None
    if root is None:
        root = core_paths.global_root()
    life_dir = core_paths.session_state_root(sid, root=root)

    # Persist transcript — write errors become the user-visible acknowledgement.
    # We inline the write because the public append_turn() swallows exceptions
    # by design for chat turns; here we intentionally let I/O errors surface.
    import json as _json

    transcript_path = life_dir / "transcript.jsonl"
    rec = {"ts": time.time(), "role": "argus", "text": text}
    try:
        life_dir.mkdir(parents=True, exist_ok=True)
        with transcript_path.open("a", encoding="utf-8") as fh:
            if is_cancelled():
                return ""
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        text = (
            "The mission is queued, but I couldn't save its confirmation. "
            "It remains in the queue."
        )
        result["ack_error"] = text
        result["ack_diagnostic"] = f"{transcript_path}: {exc}"

    if is_cancelled():
        return ""
    if callable(on_fragment):
        try:
            on_fragment("delta", {
                "text": text,
                "message_id": "dispatch",
                "fragment_mode": "snapshot",
            })
        except Exception:  # noqa: BLE001 — UI progress must never break dispatch
            pass
    else:
        # Blocking callers rely on the shared Activity stream for the live echo.
        message_id = f"dispatch-{uuid.uuid4().hex}"
        _emit_ui_turn(life_dir, "argus", text, message_id=message_id)

    result["reply"] = text
    return text
