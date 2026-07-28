"""Pending-question resolution helpers for the Manager webapi bridge.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Handles turning a raw operator reply into a pending-question
decision, resolving it through the Manager, and recording task-dispatch
acknowledgements. Public names are re-exported from ``manager_bridge``
unchanged so existing imports/monkeypatches keep working.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core import paths as core_paths
from .manager_state import _chat_state_for


def _bridge():
    """Lazily resolve ``manager_bridge`` so tests that monkeypatch
    ``manager_bridge._lock_for`` (etc.) still take effect for calls made
    from this module."""
    from . import manager_bridge

    return manager_bridge


def _emit_ui_turn(life_dir: Path, role: str, text: str, *, message_id: str) -> None:
    """Persist one operator/Manager turn onto the shared live Activity stream."""
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=life_dir).append(
            {
                "type": f"ui.{role}",
                "agent_layer": "manager" if role == "argus" else "operator",
                "message_id": message_id,
                "text": text,
                "ts": time.time(),
            }
        )
    except Exception:  # noqa: BLE001 — Activity mirroring must never break chat
        pass


_PQ_KEYS = ("IS_ANSWER", "RESOLVED", "DECISION", "REPLY")


def _named_pending_question_decision(text: str) -> dict[str, Any] | None:
    """The Manager's ruling as stated on named lines, or ``None`` if absent.

    Both booleans must actually be present. Defaulting a missing one to False
    would turn any reply this reader could not understand into a confident
    "that was not an answer", which is the operator being told their message was
    ignored because we failed to read our own role's output.

    DECISION and REPLY are read as blocks: an instruction for the Planner is
    prose and regularly spans lines.
    """
    from ..core.role_reply import read_block, read_key_values

    values = read_key_values(text, _PQ_KEYS)
    if "IS_ANSWER" not in values or "RESOLVED" not in values:
        return None
    truthy = {"true", "yes", "y", "1", "on"}
    falsy = {"false", "no", "n", "0", "off"}
    raw_answer = values["IS_ANSWER"].strip().casefold()
    raw_resolved = values["RESOLVED"].strip().casefold()
    if raw_answer not in truthy | falsy or raw_resolved not in truthy | falsy:
        return None
    is_answer = raw_answer in truthy
    resolved = raw_resolved in truthy
    decision = read_block(text, "DECISION", _PQ_KEYS).strip()
    reply = read_block(text, "REPLY", _PQ_KEYS).strip()
    if resolved and (not is_answer or not decision):
        return None
    return {
        "is_answer": is_answer,
        "resolved": resolved,
        "decision": decision,
        "reply": reply,
    }


def _parse_pending_question_decision(text: str) -> dict[str, Any] | None:
    named = _named_pending_question_decision(text)
    if named is not None:
        return named
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("is_answer"), bool)
            or not isinstance(payload.get("resolved"), bool)
        ):
            continue
        decision = str(payload.get("decision") or "").strip()
        reply = str(payload.get("reply") or "").strip()
        if payload["resolved"] and (not payload["is_answer"] or not decision):
            continue
        return {
            "is_answer": payload["is_answer"],
            "resolved": payload["resolved"],
            "decision": decision,
            "reply": reply,
        }
    return None


def _resolve_pending_question_with_manager(
    mem: Any,
    item: Any,
    answer: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> dict[str, Any]:
    from ..apps._inbox import queue_inbox_message
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink
    from ..manager.front_door import manager_triage
    from ..roles.prompts.manager import build_pending_question_prompt

    question = str(getattr(item, "pending_question", "") or "").strip()
    prompt = build_pending_question_prompt(item, answer)
    try:
        manager_reply = manager_triage(
            mem,
            prompt,
            chat_state,
            route="simple",
            root_task_id=root_task_id,
            on_fragment=None,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "error": (
                "Manager could not interpret the pending-question response: "
                f"{type(exc).__name__}: {exc}"
            ),
            "answered_item_id": item.id,
        }
    parsed = _parse_pending_question_decision(manager_reply or "")
    if parsed is None:
        return {
            "error": "Manager could not produce a valid pending-question decision",
            "answered_item_id": item.id,
        }
    if not parsed["is_answer"]:
        return {
            "answered_item_id": item.id,
            "answer_intent": False,
            "resolved": False,
            "reply": "",
        }
    if not parsed["resolved"]:
        return {
            "answered_item_id": item.id,
            "answer_intent": True,
            "resolved": False,
            "reply": parsed["reply"] or "Please clarify the requested decision.",
        }

    blocked, continuation = mem.backlog.continue_with_operator_reply(
        item.id,
        answer,
        manager_decision=parsed["decision"],
    )
    if blocked is None:
        return {"error": "unknown backlog item", "answered_item_id": item.id}
    if continuation is None:
        return {
            "error": "question is no longer pending",
            "answered_item_id": item.id,
        }

    life_dir = Path(mem.project_root)
    directive = (
        "[MANAGER OPERATOR-ANSWER DECISION] "
        f"Blocked item {item.id} was answered and continuation {continuation.id} "
        f"was durably enqueued with this decision: {parsed['decision']} "
        "Treat this as authority/context and deactivate any stale waiting contract. "
        "Do not enqueue duplicate work if that continuation is already terminal."
    )
    queue_inbox_message(life_dir, directive, source="manager.answer")
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
        "item_id": item.id,
        "continuation_item_id": continuation.id,
        "question": question,
        "manager_decision": parsed["decision"],
    })
    return {
        "answered_item_id": item.id,
        "answer_intent": True,
        "resolved": True,
        "reply": parsed["reply"] or "I have delivered your decision to the team.",
        "manager_decision": parsed["decision"],
        "item": continuation.to_jsonable(),
    }


def manager_answer_pending_question(
    sid: str,
    item_id: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Have Manager interpret and atomically deliver one operator answer."""
    from ..core.transcript import append_turn
    from ..life.memory import MemoryBundle

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _bridge()._lock_for(sid):
        if not mem.project_root.is_dir():
            return None
        item = next((row for row in mem.backlog.all() if row.id == item_id), None)
        if item is None:
            return None
        if not str(item.pending_question or "").strip():
            return {"error": "question is no longer pending"}
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        turn_id = f"web-{time.time_ns()}"
        append_turn(mem.project_root, "operator", text.strip())
        _emit_ui_turn(
            mem.project_root,
            "operator",
            text.strip(),
            message_id=f"{turn_id}-operator",
        )
        result = _resolve_pending_question_with_manager(
            mem,
            item,
            text,
            chat_state,
        )
        reply = str(
            result.get("reply")
            or result.get("error")
            or "Manager could not resolve the pending question."
        )
        append_turn(mem.project_root, "argus", reply)
        _emit_ui_turn(
            mem.project_root,
            "argus",
            reply,
            message_id=f"{turn_id}-argus",
        )
        return result


def record_task_dispatch_ack(
    sid: str,
    result: dict[str, Any],
    *,
    global_root: Path | str | None = None,
    on_fragment: Any = None,
) -> str:
    """Derive truthful acknowledgement text from the daemon-start outcome,
    persist it durably (transcript + UI event + optional SSE delta), and set
    ``result["reply"]``.

    Unlike chat turns, transcript write failures are NOT swallowed — the caller
    must surface them (the operator deserves to know their dispatch was not
    recorded).

    Called after ``start_project_daemon`` in both blocking and streaming
    endpoints.
    """
    import uuid

    daemon = result.get("daemon")
    daemon_alive = result.get("daemon_alive", False)

    # Derive truthful human-readable text
    if daemon is None and daemon_alive:
        text = "executor already running"
    elif isinstance(daemon, dict):
        if daemon.get("admission_required"):
            text = "waiting for an executor slot"
        elif int(daemon.get("rc", 0)) != 0:
            error = daemon.get("error", "unknown error")
            text = f"executor failed to start: {error}"
        else:
            text = "executor started"
    else:
        text = "executor started"

    # Resolve life_dir
    root = Path(global_root) if global_root else None
    if root is None:
        root = core_paths.global_root()
    life_dir = core_paths.session_state_root(sid, root=root)

    # Persist transcript — errors propagate (not swallowed).
    # We inline the write because the public append_turn() swallows exceptions
    # by design for chat turns; here we intentionally let I/O errors surface.
    import json as _json

    life_dir.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "role": "argus", "text": text}
    with (life_dir / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")

    # Persist UI event (best-effort — Activity mirroring must not break dispatch)
    message_id = f"dispatch-{uuid.uuid4().hex}"
    _emit_ui_turn(life_dir, "argus", text, message_id=message_id)

    # SSE delta for streaming callers
    if callable(on_fragment):
        try:
            on_fragment("delta", {
                "text": text,
                "message_id": "dispatch",
                "fragment_mode": "snapshot",
            })
        except Exception:  # noqa: BLE001 — UI progress must never break dispatch
            pass

    result["reply"] = text
    return text
