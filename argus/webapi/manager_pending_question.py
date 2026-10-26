"""Pending-question resolution helpers for the Manager webapi bridge.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Handles turning a raw operator reply into a pending-question
decision and resolving it through the Manager. Task-dispatch acknowledgements
are re-exported for compatibility. Public names are re-exported from ``manager_bridge``
unchanged so existing imports/monkeypatches keep working.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core.mission_view import conversation_is_chinese, say, sentences, uses_cjk
from .manager_dispatch_receipt import record_task_dispatch_ack as record_task_dispatch_ack


def _bridge():
    """Lazily resolve ``manager_bridge`` so tests that monkeypatch
    ``manager_bridge._lock_for`` (etc.) still take effect for calls made
    from this module."""
    from . import manager_bridge

    return manager_bridge


def _emit_ui_turn(
    life_dir: Path,
    role: str,
    text: str,
    *,
    message_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist one operator/Manager turn onto the shared live Activity stream."""
    if role != "argus":
        from .manager_followups import PRESHOWN_OPERATOR_IDS

        if message_id in PRESHOWN_OPERATOR_IDS.get():
            return
    try:
        from ..life.event_log import JsonlEventSink

        event = {
            "type": "ui.argus" if role == "argus" else "ui.operator",
            "agent_layer": "manager" if role == "argus" else "operator",
            "message_id": message_id,
            "text": text,
            "ts": time.time(),
        }
        if metadata:
            event.update(metadata)
        JsonlEventSink(None, life_dir=life_dir).append(event)
    except Exception:  # noqa: BLE001 — Activity mirroring must never break chat
        pass


def _decision_stale(reason: str) -> dict[str, Any]:
    return {
        "error": f"stale decision: {reason}",
        "application_status": "stale",
    }


def campaign_stop_reason(project_root: Path | str) -> str:
    """Why a disarmed campaign stopped, or "" while nothing holds it.

    A pause, a stop, a completion or a Planner hold each disarm the campaign
    with a ``done_reason``, and only an explicit resume re-arms it. A worker
    that ended waiting for an answer leaves the campaign armed instead.
    """
    from ..daemon.state import read_continuous_state

    state = read_continuous_state(Path(project_root))
    return "" if state.enabled else state.done_reason.strip()


def _keep_recorded_campaign_stop(mem: Any, result: dict[str, Any]) -> bool:
    """Leave a recorded campaign stop in force after an answer.

    Answering one card answers that question; it is not a resume. The answer
    reaches its task, but nothing restarts until the operator resumes, and the
    reply says so. Returns whether a stop holds.
    """
    if not campaign_stop_reason(mem.project_root):
        return False
    result["resume_requested"] = False
    reply = str(result.get("reply") or "").strip()
    chinese = uses_cjk(reply)
    stays = say("campaign_stays_stopped", chinese)
    if stays not in reply:
        result["reply"] = sentences(chinese, reply, stays)
    return True


def _is_host_drop_option(item: Any, card: dict[str, Any], option_id: str) -> bool:
    """Whether ``option_id`` is the host's "drop this task" choice on ``card``.

    The provider-configuration card offers resume/drop. "Drop" must end that
    one item; delivering its text as an authoritative decision used to enqueue
    a continuation and start another run under the broken configuration. An
    agent-authored option that happens to be called "drop" keeps its ordinary
    meaning, so only the host's card qualifies.
    """
    if option_id != "drop":
        return False
    if card.get("decision_kind") == "provider_configuration":
        return True
    # Cards parked before ``decision_kind`` was recorded: only the host's
    # provider card offers exactly resume/drop on a provider-cooldown item.
    option_ids = [
        str(row.get("id") or "")
        for row in card.get("options", ())
        if isinstance(row, dict)
    ]
    return option_ids == ["resume", "drop"] and bool(
        str(getattr(item, "provider_cooldown_signature", "") or "").strip()
    )


def _record_decision_turns(
    mem: Any, turn_id: str, *, decided_by: str, choice: str, reply: str,
) -> None:
    """The choice in its decider's name: the operator's own turn, or the Manager's."""
    from ..core.transcript import append_turn

    turns = (
        (("operator", choice), ("argus", reply))
        if decided_by == "operator"
        else ((
            "argus",
            say("answered_for_operator", uses_cjk(choice, reply), choice=choice) + f"\n\n{reply}",
        ),)
    )
    for role, text in turns:
        append_turn(mem.project_root, role, text, message_id=f"{turn_id}-{role}")
        _emit_ui_turn(mem.project_root, role, text, message_id=f"{turn_id}-{role}")


def _drop_decision_item(
    mem: Any,
    item: Any,
    *,
    decision_id: str,
    note: str,
    decided_by: str = "operator",
) -> dict[str, Any]:
    """Resolve a host "drop" choice by ending only its item; no continuation."""
    from ..core.event_catalog import EventType
    from ..core.operator_decision import selected_decision_text
    from ..life.event_log import JsonlEventSink

    operator_text = selected_decision_text(item.operator_decision, "drop", note)
    revision = int(item.operator_decision.get("revision", 1) or 1)
    # Dropping one task is not a resume: an armed campaign (one whose worker
    # ended waiting for this answer included) continues; a recorded pause,
    # stop, completion or Planner hold stands, as after any answer.
    stopped_for = campaign_stop_reason(mem.project_root)
    reply = say(
        "answer_dropped_while_stopped" if stopped_for else "answer_dropped",
        uses_cjk(operator_text, item.pending_question, item.title),
    )
    dropped = mem.backlog.stop_for_operator_decision(
        item.id,
        note=note,
        decision_id=decision_id,
        selected_option="drop",
        manager_decision="drop task",
        reply=reply,
        last_error=f"{decided_by} dropped this task",
        decided_by=decided_by,
    )
    if dropped is None:
        # Another answer resolved this card first; report that, never overwrite it.
        current = next(
            (row for row in mem.backlog.history() if row.id == item.id), item,
        )
        replay = _resolved_decision_replay(
            current, decision_id=decision_id, option_id="drop", note=note,
        )
        return replay or _decision_stale("decision changed while applying")
    card = dropped.operator_decision
    resumed, projection_error = (
        (False, "")
        if stopped_for
        else _reconcile_campaign_after_decision(mem, stopped=False)
    )
    _record_decision_turns(
        mem, f"decision-{decision_id}", decided_by=decided_by,
        choice=operator_text, reply=reply,
    )
    JsonlEventSink(None, life_dir=Path(mem.project_root)).append({
        "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
        "item_id": item.id,
        "question": str(item.pending_question or ""),
        "manager_decision": "drop task",
        "decision_id": decision_id,
        "decision_revision": revision,
        "dropped": True,
        "decided_by": decided_by,
    })
    result = {
        "answered_item_id": item.id,
        "decision_id": decision_id,
        "resolved": True,
        "dropped": True,
        "application_status": "accepted",
        "resolution_id": str(card.get("resolution_id") or ""),
        # The dropped item does not resume; the rest of the campaign does,
        # unless something recorded holds it.
        "resume_requested": not stopped_for,
        "reply": reply,
    }
    if resumed:
        result["continuous"] = True
    if projection_error:
        result["projection_error"] = projection_error
    return result


def _resolved_decision_replay(
    item: Any,
    *,
    decision_id: str,
    option_id: str,
    note: str,
) -> dict[str, Any] | None:
    """Return an idempotent result for the exact choice already on disk."""
    card = item.operator_decision
    if str(card.get("id") or "") != decision_id:
        if decision_id in card.get("superseded_decision_ids", ()):
            return _decision_stale(
                "the question changed after this answer was written; read the "
                "current question and answer it again"
            )
        return None
    if str(card.get("status") or "") != "resolved":
        return None
    same_request = (
        str(card.get("id") or "") == decision_id
        and str(card.get("selected_option") or "") == option_id
        and str(card.get("note") or "").strip() == note.strip()
    )
    if not same_request:
        return _decision_stale("another choice was already applied")
    if _is_host_drop_option(item, card, option_id):
        return {
            "answered_item_id": item.id,
            "decision_id": decision_id,
            "resolved": True,
            "dropped": True,
            "application_status": "already_applied",
            "resolution_id": str(card.get("resolution_id") or ""),
            "resume_requested": False,
            "reply": str(card.get("reply") or say("answer_dropped", uses_cjk(card.get("question")))),
        }
    if card.get("decision_kind") == "framework_deployment":
        return {
            "answered_item_id": item.id,
            "decision_id": decision_id,
            "resolved": True,
            "application_status": "already_applied",
            "resolution_id": str(card.get("resolution_id") or ""),
            "resume_requested": False,
            "reply": str(card.get("reply") or ""),
            "deployment": dict(card.get("deployment") or {}),
            "maintenance_cleanup": dict(card.get("maintenance_cleanup") or {}),
        }
    result: dict[str, Any] = {
        "answered_item_id": item.id,
        "decision_id": decision_id,
        "resolved": True,
        "application_status": "already_applied",
        "resolution_id": str(card.get("resolution_id") or ""),
        "resume_requested": bool(card.get("resume_requested", option_id != "stop")),
        "reply": str(card.get("reply") or "").strip() or say(
            "answer_stopped" if option_id == "stop" else "answer_already_taken",
            uses_cjk(card.get("question")),
        ),
    }
    if option_id == "stop":
        result["stopped"] = True
    else:
        result["manager_decision"] = str(card.get("manager_decision") or "")
        result["item"] = item.to_jsonable()
    return result


def _reconcile_campaign_after_decision(
    mem: Any,
    *,
    stopped: bool,
) -> tuple[bool, str]:
    """Project a resolved card onto continuous state; safe to call again."""
    from ..daemon.state import read_continuous_state, write_continuous_config

    before = read_continuous_state(mem.project_root)
    if stopped:
        if before.enabled:
            write_continuous_config(
                mem.project_root,
                enabled=False,
                objective=before.objective,
                done_reason="operator chose to stop the campaign",
            )
        after = read_continuous_state(mem.project_root)
        if after.enabled:
            return False, "campaign stop is recorded but continuous state is still enabled"
        return False, ""
    if before.objective.strip() and not before.enabled:
        write_continuous_config(
            mem.project_root,
            enabled=True,
            objective=before.objective,
        )
        after = read_continuous_state(mem.project_root)
        if not after.enabled:
            return False, "decision is recorded but continuous resume is still pending"
        return True, ""
    return False, ""


def _apply_framework_deployment_decision(
    mem: Any,
    item: Any,
    *,
    option_id: str,
    decision_id: str,
    note: str,
    decided_by: str = "operator",
) -> dict[str, Any]:
    """Apply the existing adopt/decline decision without another validation run."""
    import subprocess

    from ..core.secret_guard import redact_secrets_text
    from ..life.event_log import JsonlEventSink
    from ..life.supervisor._mission_execution_runtime import (
        _maintenance_sidecar_path,
        dispose_maintenance_worktree,
    )

    sidecar = _maintenance_sidecar_path(
        mem.global_root, item.id, fallback_root=mem.project_root,
    )
    card = dict(item.operator_decision)
    revision = int(card.get("revision", 1) or 1)
    if option_id == "decline":
        reply = "The reviewed change was declined. The current runtime is unchanged."
        deployment: dict[str, Any] = {"verdict": "DECLINED"}
        status = "aborted"
        last_error = f"{decided_by} declined the reviewed framework change"
    else:
        from ..daemon.handoff import request_deployment_handoff
        from ..maintenance.publication import publish_reviewed_change

        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            candidate = str(metadata["reviewed_candidate"])
            if metadata.get("mission_id") != item.id or metadata.get("reviewer_verdict") != "done":
                raise ValueError("the stored change does not belong to this reviewed task")
            if card.get("reviewed_candidate", candidate) != candidate:
                raise ValueError("the candidate changed after this decision was created")
            # The daemon consumes handoff requests from its project life_dir,
            # even when the authoring sidecar lives in the shared state root.
            receipt_dir = Path(mem.project_root) / "maintenance" / "receipts"
            deployment = publish_reviewed_change(Path(metadata["repository"]), candidate, receipt_dir)
            request_deployment_handoff(receipt_dir, Path(deployment["runtime_source_root"]))
        except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            reason = redact_secrets_text(str(exc))[-1200:]
            mem.backlog.update(item.id, last_error=reason)
            return {
                "answered_item_id": item.id, "decision_id": decision_id,
                "resolved": False, "application_status": "retryable", "resume_requested": False,
                "error": "Could not apply the reviewed change: " + reason
                    + ". The runtime has not been switched; the reviewed work is retained.",
            }
        reply = (
            "The reviewed commit is published on origin/main. "
            "The daemon will adopt it at a task boundary."
        )
        status, last_error = "done", ""

    card.update({
        "status": "resolved", "selected_option": option_id, "note": note.strip(),
        "resolved_from_revision": revision, "revision": revision + 1,
        "resume_requested": False,
        "resolution_id": f"{card.get('id', decision_id)}:r{revision}",
        "reply": reply, "deployment": deployment, "decided_by": decided_by,
    })
    mem.backlog.update(
        item.id, status=status, finished_ts=time.time(), last_error=last_error,
        pending_question="", operator_decision=card,
    )
    cleanup: dict[str, Any] = {"status": "not_found", "sidecar_path": str(sidecar)}
    had_sidecar = sidecar.is_file()
    try:
        dispose_maintenance_worktree(sidecar.parents[2], item.id)
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        cleanup.update(status="retained", reason=str(exc))
    else:
        if had_sidecar:
            cleanup["status"] = "removed"
    cleanup["sidecar_retained"] = sidecar.is_file()
    card["maintenance_cleanup"] = cleanup
    mem.backlog.update(item.id, operator_decision=card)
    JsonlEventSink(None, life_dir=Path(mem.project_root)).append({
        "type": "life.operator_question.answered", "item_id": item.id,
        "question": str(item.pending_question or ""),
        "manager_decision": option_id, "decision_id": decision_id,
        "decision_revision": revision, "deployment": deployment, "maintenance_cleanup": cleanup,
        "decided_by": decided_by,
    })
    return {
        "answered_item_id": item.id, "decision_id": decision_id, "resolved": True,
        "application_status": "accepted", "resolution_id": card["resolution_id"],
        "resume_requested": False, "reply": reply,
        "maintenance_cleanup": cleanup, "deployment": deployment,
    }


def _apply_operator_answer(
    mem: Any,
    item: Any,
    answer: str,
    *,
    manager_decision: str,
    manager_reply: str = "",
    decision_option: str = "custom",
    decision_id: str = "",
    decision_note: str = "",
    operator_context_persisted: bool = False,
    operator_context_revision: int = 0,
    decided_by: str = "operator",
) -> dict[str, Any]:
    """Persist an explicit answer and resume the task it parked.

    ``decided_by`` is ``"manager"`` when the Manager answered in the operator's
    place; the answer is then never recorded as the operator's.
    """
    if item.operator_decision.get("decision_kind") == "framework_deployment":
        option_id = decision_option.strip().lower()
        if option_id == "custom":
            option_id = answer.strip().lower()
        available_options = {
            str(option.get("id") or "")
            for option in item.operator_decision.get("options", ())
            if isinstance(option, dict)
        }
        if option_id not in available_options:
            return {
                "error": "choose an available deployment option",
                "answered_item_id": item.id,
            }
        return _apply_framework_deployment_decision(
            mem,
            item,
            option_id=option_id,
            decision_id=(decision_id or str(item.operator_decision.get("id") or "")),
            note=decision_note,
            decided_by=decided_by,
        )

    from ..apps._inbox import queue_inbox_message
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    question = str(item.pending_question or "").strip()
    # Typed words are the operator's own language. A clicked option is in the card's words
    # (often code or English), so for a click the question's language decides.
    clicked = decision_option.strip().lower() not in ("", "custom")
    zh = conversation_is_chinese(question, answer) if clicked else conversation_is_chinese(answer, question)
    manager_reply = manager_reply or say("answer_taken", zh)
    resumed, answered = mem.backlog.resume_with_operator_reply(
        item.id,
        answer,
        manager_decision=manager_decision,
        decision_option=decision_option,
        decision_id=decision_id,
        decision_note=decision_note,
        manager_reply=manager_reply,
        operator_context_persisted=operator_context_persisted,
        decided_by=decided_by,
    )
    if resumed is None:
        return {"error": "unknown backlog item", "answered_item_id": item.id}
    if not answered:
        if decision_id:
            replay = _resolved_decision_replay(
                resumed,
                decision_id=decision_id,
                option_id=decision_option,
                note=decision_note,
            )
            if replay is not None:
                return replay
        return {
            "error": "question is no longer pending",
            "answered_item_id": item.id,
        }

    life_dir = Path(mem.project_root)
    directive = (
        "[MANAGER OPERATOR-ANSWER DECISION] "
        f"Task {item.id} was answered and resumes with this decision: {manager_decision} "
        "Treat this as authority/context and deactivate any stale waiting contract. "
        "Do not enqueue duplicate work for that task."
    )
    queue_inbox_message(life_dir, directive, source="manager.answer")
    # The option chosen, in the words the card offered it; none for a written answer.
    chosen = next((
        str(option.get("label") or "")
        for option in item.operator_decision.get("options", ())
        if isinstance(option, dict) and option.get("id") == decision_option
    ), "")
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
        "item_id": item.id,
        "question": question,
        "manager_decision": manager_decision,
        **({"option": chosen} if chosen else {}),
        "decision_id": decision_id,
        "decision_revision": resumed.operator_decision.get(
            "resolved_from_revision"
        ),
        "decided_by": decided_by,
    })
    if operator_context_revision:
        from ..core.operator_context import OperatorContextStore

        OperatorContextStore(life_dir).settle_once(operator_context_revision)
    return {
        "answered_item_id": item.id,
        "answer_intent": True,
        "resolved": True,
        "application_status": "accepted",
        "resolution_id": str(
            resumed.operator_decision.get("resolution_id") or ""
        ),
        "resume_requested": True,
        "reply": manager_reply,
        "manager_decision": manager_decision,
        "item": resumed.to_jsonable(),
    }


def apply_manager_answer(mem: Any, handoff: Any, operator_text: str, *, reply: str = "") -> dict[str, Any]:
    """Apply the operator's answer the Manager read off its turn (``answer`` of the ``manager`` form).

    The form's check already tied it to a task waiting on a question. ``reply``
    is the Manager's own words to the operator in that turn: when the answer
    applies they are its one acknowledgement (``result["reply"]``), not a second
    line after them; a canned line stands in only when the Manager said nothing.
    """
    from ..core.operator_context import persist_once_answer

    answer = handoff.get("answer")
    item = next(row for row in mem.backlog.active() if row.id == answer["item"])
    record = persist_once_answer(
        mem.project_root, operator_text, source="operator.pending_answer", mission_id=str(item.id),
    )
    # The reply speaks to the person; ``summary`` is the Manager's note for the team.
    options = (item.operator_decision or {}).get("options") or ()
    picked = next((str(option.get("label") or "").strip() for option in options
                   if isinstance(option, dict) and option.get("id") == answer.get("option")), "")
    zh = conversation_is_chinese(operator_text, item.pending_question, item.title)
    result = _apply_operator_answer(
        mem, item, operator_text, manager_decision=answer["decision"],
        manager_reply=reply.strip() or (
            say("answer_continue_with", zh, choice=picked) if picked else say("answer_taken", zh)
        ),
        decision_option=answer.get("option", "custom"),
        operator_context_persisted=True, operator_context_revision=record.revision,
    )
    if result.get("resolved") and result.get("resume_requested"):
        _keep_recorded_campaign_stop(mem, result)
    return result


def manager_answer_pending_question(
    sid: str,
    item_id: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    decision_option: str = "custom",
    decision_id: str = "",
    decision_note: str = "",
) -> dict[str, Any] | None:
    """Treat an explicit operator answer as authoritative and continue the item."""
    from ..core.transcript import append_turn
    from ..life.memory import MemoryBundle

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _bridge()._lock_for(sid):
        if not mem.project_root.is_dir():
            return None
        item = next((row for row in mem.backlog.history() if row.id == item_id), None)
        if item is None:
            return None
        if not str(item.pending_question or "").strip():
            if decision_id:
                replay = _resolved_decision_replay(
                    item,
                    decision_id=decision_id,
                    option_id=decision_option,
                    note=decision_note,
                )
                if replay is not None:
                    if replay.get("resume_requested"):
                        _keep_recorded_campaign_stop(mem, replay)
                    return replay
            return {"error": "question is no longer pending"}
        turn_id = (
            f"decision-{decision_id}"
            if decision_id
            else f"web-{time.time_ns()}"
        )
        from ..core.operator_context import import_deterministic_credential

        text, _credential = import_deterministic_credential(
            mem.project_root,
            text,
            global_root=mem.global_root,
        )
        append_turn(
            mem.project_root,
            "operator",
            text.strip(),
            message_id=f"{turn_id}-operator",
        )
        _emit_ui_turn(
            mem.project_root,
            "operator",
            text.strip(),
            message_id=f"{turn_id}-operator",
        )
        from ..core.operator_context import persist_once_answer

        answer_record = persist_once_answer(
            mem.project_root,
            text.strip(),
            source="operator.explicit_answer",
            mission_id=str(item.id),
        )
        result = _apply_operator_answer(
            mem,
            item,
            text.strip(),
            manager_decision=text.strip(),
            decision_option=decision_option,
            decision_id=decision_id,
            decision_note=decision_note,
            operator_context_persisted=True,
            operator_context_revision=answer_record.revision,
        )
        if (
            result.get("resolved")
            and result.get("resume_requested", True)
            and not _keep_recorded_campaign_stop(mem, result)
        ):
            resumed, projection_error = _reconcile_campaign_after_decision(
                mem,
                stopped=False,
            )
            if resumed:
                result["continuous"] = True
            if projection_error:
                result["projection_error"] = projection_error
        # A failed answer keeps its diagnostic in ``error``; the operator
        # reads what happened to their answer.
        reply = str(result.get("reply") or "").strip() or say("answer_not_applied", uses_cjk(text))
        append_turn(
            mem.project_root,
            "argus",
            reply,
            message_id=f"{turn_id}-argus",
        )
        _emit_ui_turn(
            mem.project_root,
            "argus",
            reply,
            message_id=f"{turn_id}-argus",
        )
        return result


def manager_resolve_operator_decision(
    sid: str,
    decision_id: str,
    option_id: str,
    note: str = "",
    *,
    global_root: Path | str | None = None,
    decided_by: str = "operator",
    reason: str = "",
) -> dict[str, Any] | None:
    """Resolve one visible decision-card option with idempotent replay.

    The Manager may choose (``decided_by="manager"``, with its ``reason``) only
    while the operator cannot be asked; the choice is then applied exactly as
    the operator's would be and recorded in the Manager's name.
    """
    from ..core.operator_decision import selected_decision_text
    from ..life.memory import MemoryBundle

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    if decided_by == "manager":
        from ..core.autonomy import operator_questions_can_be_asked

        if operator_questions_can_be_asked(mem.project_root):
            return {"error": "the operator can be asked; this decision is theirs"}
    with _bridge()._lock_for(sid):
        item = next(
            (
                row
                for row in mem.backlog.history()
                if (
                    str(row.operator_decision.get("id") or "") == decision_id
                    or decision_id
                    in row.operator_decision.get("superseded_decision_ids", ())
                )
            ),
            None,
        )
        if item is None:
            return None

        replay = _resolved_decision_replay(
            item,
            decision_id=decision_id,
            option_id=option_id,
            note=note,
        )
        if replay is not None:
            if replay.get("application_status") == "already_applied" and (
                option_id == "stop"
                or (
                    replay.get("resume_requested", True)
                    and not _keep_recorded_campaign_stop(mem, replay)
                )
            ):
                resumed, projection_error = _reconcile_campaign_after_decision(
                    mem,
                    stopped=option_id == "stop",
                )
                if resumed:
                    replay["continuous"] = True
                if projection_error:
                    replay["projection_error"] = projection_error
            return replay

        card = item.operator_decision
        if (
            str(card.get("status") or "") != "pending"
            or not str(item.pending_question or "").strip()
        ):
            conflict = _decision_stale("decision is no longer pending")
            conflict["decision_id"] = decision_id
            conflict["answered_item_id"] = item.id
            return conflict
        if (
            card.get("decision_kind") == "framework_deployment"
            and option_id not in {"adopt", "decline"}
        ):
            return {"error": "choose Adopt reviewed change or Decline deployment"}
        if _is_host_drop_option(item, card, option_id):
            return _drop_decision_item(
                mem, item, decision_id=decision_id, note=note, decided_by=decided_by,
            )
        if option_id == "stop":
            try:
                operator_text = selected_decision_text(card, option_id, note)
            except ValueError as exc:
                return {"error": str(exc)}
            stopped = mem.backlog.stop_for_operator_decision(
                item.id,
                note=note,
                decision_id=decision_id,
                reply=say("answer_stopped", uses_cjk(operator_text, card.get("question"))),
                decided_by=decided_by,
            )
            if stopped is None:
                current = next(
                    (row for row in mem.backlog.history() if row.id == item.id),
                    item,
                )
                replay = _resolved_decision_replay(
                    current,
                    decision_id=decision_id,
                    option_id=option_id,
                    note=note,
                )
                return replay or _decision_stale("decision changed while applying")
            _resumed, projection_error = _reconcile_campaign_after_decision(
                mem,
                stopped=True,
            )
            result = {
                "answered_item_id": item.id,
                "resolved": True,
                "stopped": True,
                "decision_id": decision_id,
                "application_status": "accepted",
                "resolution_id": str(
                    stopped.operator_decision.get("resolution_id") or ""
                ),
                "resume_requested": False,
                "reply": str(stopped.operator_decision.get("reply") or ""),
            }
            if projection_error:
                result["projection_error"] = projection_error
            from ..core.event_catalog import EventType
            from ..life.event_log import JsonlEventSink

            _record_decision_turns(
                mem, f"decision-{decision_id}", decided_by=decided_by,
                choice=operator_text, reply=result["reply"],
            )
            JsonlEventSink(None, life_dir=mem.project_root).append({
                "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
                "item_id": item.id,
                "question": str(item.pending_question or ""),
                "manager_decision": "stop campaign",
                "decision_id": decision_id,
                "decision_revision": stopped.operator_decision.get(
                    "resolved_from_revision"
                ),
                "stopped": True,
                "decided_by": decided_by,
            })
            return result
        try:
            answer = selected_decision_text(card, option_id, note)
        except ValueError as exc:
            return {"error": str(exc)}
        if decided_by == "manager":
            result = _apply_operator_answer(
                mem, item, answer,
                manager_decision=answer,
                manager_reply=reason,
                decision_option=option_id,
                decision_id=decision_id,
                decision_note=note,
                decided_by=decided_by,
            )
            if result.get("resolved"):
                _record_decision_turns(
                    mem, f"decision-{decision_id}", decided_by=decided_by,
                    choice=answer, reply=reason,
                )
            result["decision_id"] = decision_id
            return result
        result = manager_answer_pending_question(
            sid,
            item.id,
            answer,
            global_root=global_root,
            decision_option=option_id,
            decision_id=decision_id,
            decision_note=note,
        )
        if result is not None:
            result["decision_id"] = decision_id
        return result
