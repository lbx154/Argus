"""A team hand-off that arrives while a mission runs: queued, never timed out.

The person's message gets the Manager's reply at once. The ticket it hands the
team is written to ``<life_dir>/manager-handoffs/<id>.json`` and applied at the
running mission's next safe point: the queue asks the daemon to yield
(``request_manager_pipeline_yield``), so no not-yet-started work begins before
the hand-off is in, and waits for the pipeline without a deadline. Live trial
s-efe9deac (2026-10-26): the hand-off waited 15 minutes behind a research
mission, timed out, and the person read an English "nothing was queued"
instead of the Manager's reply.

A queued record left by a stopped server is applied at the project's next
message (``resume_queued_handoffs``).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

QUEUE_DIR = "manager-handoffs"
_POLL_SECONDS = 1.0
_RUNNING: set[str] = set()
_RUNNING_GUARD = threading.Lock()


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def queued_handoffs(life_dir: Path | str) -> list[dict[str, Any]]:
    """The hand-offs still waiting for a safe point, oldest first."""
    rows = [_read(path) for path in sorted((Path(life_dir) / QUEUE_DIR).glob("*.json"))]
    return sorted((row for row in rows if row.get("status") == "queued"), key=lambda row: row.get("queued_at", 0))


def queue_team_handoff(
    sid: str, life_dir: Path | str, *, global_root: Path | str | None, body: str, public_task: str,
    root_task_id: str, operator_text: str, chat_patch: dict[str, Any],
    attachment_context_refs: list[dict[str, str]] | None = None, reference_deps: list[str] | None = None,
) -> dict[str, Any]:
    """Record the hand-off durably and start applying it at the next safe point."""
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    life_dir = Path(life_dir)
    record = {
        "id": f"handoff-{time.time_ns()}", "sid": sid, "status": "queued", "queued_at": time.time(),
        "global_root": str(global_root or ""), "body": body, "public_task": public_task,
        "root_task_id": root_task_id, "operator_text": operator_text,
        "attachment_context_refs": list(attachment_context_refs or []),
        "reference_deps": list(reference_deps or []),
        # Durable fields only: the Manager's routing decision object lives in
        # this process and is rebuilt from the ticket after a restart.
        "chat_patch": {key: value for key, value in chat_patch.items() if _jsonable(value)},
    }
    _write(life_dir / QUEUE_DIR / f"{record['id']}.json", record)
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_STATUS, "agent_layer": "manager", "item_id": root_task_id,
        "handoff_id": record["id"], "handoff_status": "queued",
        "text": "team hand-off queued for the running mission's next safe point",
    })
    start_applying(record, live_patch=chat_patch)
    return record


def _jsonable(value: Any) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def start_applying(record: dict[str, Any], *, live_patch: dict[str, Any] | None = None) -> bool:
    """Apply ``record`` in the background; False when it is already being applied."""
    with _RUNNING_GUARD:
        if record["id"] in _RUNNING:
            return False
        _RUNNING.add(record["id"])
    thread = threading.Thread(
        target=_apply, args=(record, dict(live_patch or record.get("chat_patch") or {})),
        name=f"manager-handoff-{record['sid']}", daemon=True,
    )
    thread.start()
    return True


def resume_queued_handoffs(life_dir: Path | str) -> int:
    """Apply the hand-offs a stopped server left queued; how many were started."""
    return sum(start_applying(record) for record in queued_handoffs(life_dir))


def _settle(life_dir: Path, record: dict[str, Any], status: str, **fields: Any) -> None:
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    record.update(status=status, settled_at=time.time(), **fields)
    _write(life_dir / QUEUE_DIR / f"{record['id']}.json", record)
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_STATUS, "agent_layer": "manager",
        "item_id": record.get("root_task_id", ""), "handoff_id": record["id"], "handoff_status": status,
        **({"error": fields["error"]} if fields.get("error") else {}),
        "text": f"queued team hand-off {status}",
    })


def _apply(record: dict[str, Any], patch: dict[str, Any]) -> None:
    from ..core.mission_view import load_mission_view, say, session_is_chinese
    from ..core.operator_messages import publish_operator_message
    from ..life.memory import MemoryBundle
    from ..manager._session_ops import (
        clear_manager_pipeline_yield,
        manager_pipeline_busy,
        request_manager_pipeline_yield,
    )
    from .manager_dispatch import _dispatch_team_mission, _TurnEmitter
    from .manager_state import _chat_state_for, _lock_for

    sid = record["sid"]
    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(record["global_root"]) if record.get("global_root") else None,
    )
    life_dir = Path(mem.project_root)
    token = ""
    try:
        chinese = session_is_chinese(load_mission_view(life_dir), record.get("operator_text", ""))
    except Exception:  # noqa: BLE001 - wording only
        chinese = False
    try:
        # The daemon yields at its next mission boundary and starts nothing new
        # while this request lives; the wait has no deadline.
        token = request_manager_pipeline_yield(life_dir)
        while manager_pipeline_busy(life_dir):
            if not life_dir.is_dir():
                return
            time.sleep(_POLL_SECONDS)
        with _lock_for(sid):
            chat_state = _chat_state_for(sid)
            chat_state.update(session_id=sid, global_root=str(mem.global_root))
            chat_state.update(patch)
            chat_state["_domain_setup_enabled"] = False
            emitter = _TurnEmitter(
                life_dir=life_dir, turn_id=record["id"], fragment=lambda _kind, _payload: None,
                task_objective=record.get("operator_text", ""),
            )
            item, alive, _pid = _dispatch_team_mission(
                mem, record["body"], chat_state, record["root_task_id"], lambda: False, emitter,
                attachment_context_refs=record.get("attachment_context_refs") or None,
                reference_deps=record.get("reference_deps") or None,
                public_objective=record.get("public_task") or None,
            )
        _settle(life_dir, record, "applied", item_id=getattr(item, "id", ""))
        if not alive:
            from .daemon_lifecycle import start_project_daemon

            start_project_daemon(sid, global_root=record.get("global_root") or None,
                                 resume_continuous=True, reclaim_idle=True)
        title = str(getattr(item, "title", "") or record.get("public_task") or "")
        publish_operator_message(
            life_dir, text=say("handoff_applied", chinese, title=title),
            message_id=f"{record['id']}-applied",
        )
    except Exception as exc:  # noqa: BLE001 - the person is told, in their language, what happens next
        log.warning("queued team hand-off %s failed", record.get("id"), exc_info=True)
        said = say("handoff_reason_unknown", chinese)
        try:
            _settle(life_dir, record, "failed", error=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            publish_operator_message(
                life_dir, text=say("handoff_failed", chinese, said=said), message_id=f"{record['id']}-failed",
            )
    finally:
        if token:
            clear_manager_pipeline_yield(life_dir, token)
        with _RUNNING_GUARD:
            _RUNNING.discard(record["id"])


__all__ = ["QUEUE_DIR", "queue_team_handoff", "queued_handoffs", "resume_queued_handoffs", "start_applying"]
