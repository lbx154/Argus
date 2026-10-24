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
from ..core.mission_view import conversation_is_chinese, say


def _dispatch_ack_text(
    result: dict[str, Any], daemon: Any, daemon_alive: bool, *, operator_text: str = "",
) -> str:
    """The first reply to a task: what happens next, without echoing the request."""
    raw_item = result.get("item")
    item = raw_item if isinstance(raw_item, dict) else {}
    # A reply like "2" says nothing about language: keep the conversation's.
    zh = conversation_is_chinese(operator_text, item.get("pending_question"), item.get("title"), item.get("objective"))
    dispatch_state = result.get("dispatch_state")
    if result.get("launch_hold"):
        # Nothing has started, so the Manager's own words on its plan close the
        # turn; the card under them holds Start and Discard.
        return str(result.get("reply") or "").strip() or say("ack_plan_awaits_start", zh)
    question = str(item.get("pending_question") or "").strip()
    if question:
        from ..life.supervisor.pending_notify import numbered_options

        options = numbered_options((item.get("operator_decision") or {}).get("options"), zh)
        return "\n\n".join(filter(None, (say("ack_needs_your_call", zh, question=question), options)))

    # The endpoint's fresh startup result outranks the bridge's earlier queue
    # observation. A daemon may disappear between those two observations.
    if isinstance(daemon, dict):
        if daemon.get("admission_required"):
            return say("ack_waits_for_slot", zh)
        if daemon.get("control_busy"):
            return say("ack_checking_start", zh)
        if int(daemon.get("rc", 0)) != 0:
            diagnostic = str(daemon.get("startup_diagnostic") or daemon.get("diagnostic")
                             or daemon.get("error") or "unknown error")
            daemon["diagnostic"] = diagnostic
            daemon["error"] = "The background worker could not start."
            return say("ack_start_failed", zh)
    if dispatch_state == "already_queued":
        status = str(item.get("status") or "pending")
        if status not in {"pending", "running", "done", "parked", "failed"}:
            status = "saved"
        return say("ack_already_asked", zh, state=say(f"asked_{status}", zh))
    if dispatch_state == "queued_after_current" and daemon_alive:
        return say("ack_after_current", zh)
    if dispatch_state == "running" and daemon_alive:
        return say("ack_picked_up", zh)
    if dispatch_state == "planner_pending":
        return say("ack_goal_updated", zh)
    return say("ack_starting", zh)


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
        text = say("ack_not_recorded", conversation_is_chinese(operator_text, text))
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
