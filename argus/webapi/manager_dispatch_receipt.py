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
from ..core.mission_view import uses_cjk


def _dispatch_ack_text(
    result: dict[str, Any], daemon: Any, daemon_alive: bool, *, operator_text: str = "",
) -> str:
    """The first reply to a task: what happens next, without echoing the request."""
    raw_item = result.get("item")
    item = raw_item if isinstance(raw_item, dict) else {}
    zh = uses_cjk(operator_text or str(item.get("title") or item.get("objective") or ""))
    dispatch_state = result.get("dispatch_state")
    if result.get("launch_hold"):
        return (
            "计划排好了，还没开始。请在上面的卡片里看看我的理解和计划，点“开始执行”后才会开始。"
            if zh else
            "The plan is ready, but nothing has started. Check what I understood and "
            "the plan in the card above; work starts when you press Start."
        )
    question = str(item.get("pending_question") or "").strip()
    if question:
        return f"开始之前需要你定一下：\n{question}" if zh else f"Before I start, I need your call:\n{question}"

    # The endpoint's fresh startup result outranks the bridge's earlier queue
    # observation. A daemon may disappear between those two observations.
    if isinstance(daemon, dict):
        if daemon.get("admission_required"):
            return (
                "记下了。现在同时在做的事已经满了，空出来就开始，不用重发。"
                if zh else
                "Got it. Everything that can run at once is busy; this starts as soon "
                "as a slot frees up, no need to resend."
            )
        if daemon.get("control_busy"):
            return (
                "记下了，正在确认能不能开始，稍后告诉你。"
                if zh else "Got it. Checking whether it can start; I'll let you know."
            )
        if int(daemon.get("rc", 0)) != 0:
            diagnostic = str(daemon.get("startup_diagnostic") or daemon.get("diagnostic")
                             or daemon.get("error") or "unknown error")
            daemon["diagnostic"] = diagnostic
            daemon["error"] = "The background worker could not start."
            return (
                "记下了，但没能开始工作。你的要求已经保存，可以点“启动详情”看原因，或者稍后再试。"
                if zh else
                "Saved, but the work couldn't start. Your request is kept; see the "
                "startup details for why, or try again later."
            )
    if dispatch_state == "already_queued":
        status = str(item.get("status") or "pending")
        state = {
            "pending": ("还没开始", "not started yet"),
            "running": ("正在做", "in progress"),
            "done": ("已经做完", "done"),
            "parked": ("暂停着", "paused"),
            "failed": ("上次没做成", "it didn't succeed last time"),
        }.get(status, ("已经记下", "saved"))
        return (
            f"这件事之前已经交代过了（{state[0]}），没有再加一遍。" if zh
            else f"You already asked for this ({state[1]}); I didn't add it again."
        )
    if dispatch_state == "queued_after_current" and daemon_alive:
        return "收到，等手头这项做完就开始。" if zh else "Got it. This starts once the current work is done."
    if dispatch_state == "running" and daemon_alive:
        return "收到，已经接手了。" if zh else "Got it, it's been picked up."
    if dispatch_state == "planner_pending":
        return "目标已更新，正在安排下一步。" if zh else "Goal updated; working out the next step."
    return "收到，马上开始。" if zh else "Got it, starting now."


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
            "任务已经记下了，只是这条确认没能保存。"
            if uses_cjk(operator_text or text)
            else "The task is saved; only this confirmation couldn't be recorded."
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
