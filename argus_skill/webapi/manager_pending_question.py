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


def _manager_failure_facts(exc: Exception) -> dict[str, Any]:
    """Preserve the most specific structured/provider cause in an exception chain."""
    current: BaseException | None = exc
    structured: BaseException | None = None
    deepest: BaseException = exc
    while current is not None:
        deepest = current
        if getattr(current, "phase", "") and structured is None:
            structured = current
        current = current.__cause__ or current.__context__
    source = structured or deepest
    raw_cause = str(getattr(source, "cause", "") or str(source)).strip()
    from ..core.secret_guard import known_secret_values, redact_secrets_text

    cause = redact_secrets_text(
        raw_cause,
        known_values=known_secret_values(),
    )
    phase = str(getattr(source, "phase", "") or "").strip()
    if not phase:
        phase = "timeout" if isinstance(source, TimeoutError) else "backend"
    backend_error = redact_secrets_text(
        str(getattr(source, "backend_error", "") or "").strip(),
        known_values=known_secret_values(),
    )
    if phase in {"backend", "timeout"} and not backend_error:
        backend_error = cause
    return {
        "phase": phase,
        "cause": cause,
        "contract_field": str(
            getattr(source, "contract_field", "") or ""
        ).strip(),
        "attempts": max(1, int(getattr(source, "attempts", 1) or 1)),
        "model_reply_snippet": str(
            getattr(source, "model_reply_snippet", "") or ""
        )[:300],
        "backend_error": backend_error,
        **(
            {"login_required": True}
            if bool(getattr(source, "login_required", False))
            else {}
        ),
    }


def _emit_pending_question_failure(
    mem: Any,
    item: Any,
    facts: dict[str, Any],
    *,
    error: str,
    answer_preserved: bool,
) -> None:
    from ..life.event_log import JsonlEventSink

    JsonlEventSink(None, life_dir=Path(mem.project_root)).append({
        "type": "life.manager.intent.failed",
        "agent_layer": "manager",
        "intent_id": f"pending-question-{item.id}-{time.time_ns()}",
        "item_id": item.id,
        "source": "operator_answer",
        "objective": f"interpret pending question answer for item {item.id}",
        "error": error,
        **facts,
        "answer_preserved": answer_preserved,
        "text": "manager pending-question interpretation failed",
    })


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
        lines = cleaned.splitlines()[1:]
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
        except ValueError:
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


def _decision_stale(reason: str) -> dict[str, Any]:
    return {
        "error": f"stale decision: {reason}",
        "application_status": "stale",
    }


def _resolved_decision_replay(
    mem: Any,
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
            return _decision_stale("decision was replaced by a newer question")
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
        }
    continuation_id = str(card.get("continuation_item_id") or "").strip()
    continuation = next(
        (row for row in mem.backlog.history() if row.id == continuation_id),
        None,
    ) if continuation_id else None
    if option_id != "stop" and continuation is None:
        return {
            "error": "resolved decision is missing its continuation item",
            "application_status": "stale",
        }
    result: dict[str, Any] = {
        "answered_item_id": item.id,
        "decision_id": decision_id,
        "resolved": True,
        "application_status": "already_applied",
        "resolution_id": str(card.get("resolution_id") or ""),
        "resume_requested": bool(card.get("resume_requested", option_id != "stop")),
        "reply": str(card.get("reply") or "").strip()
        or (
            "Campaign stopped. Current work was preserved."
            if option_id == "stop"
            else "This decision was already delivered to the team."
        ),
    }
    if option_id == "stop":
        result["stopped"] = True
    else:
        result["manager_decision"] = str(card.get("manager_decision") or "")
        result["item"] = continuation.to_jsonable()
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
) -> dict[str, Any]:
    """Resolve a reviewed maintenance card without creating another mission."""
    from ..life.event_log import JsonlEventSink
    from ..life.supervisor._mission_execution_runtime import (
        dispose_maintenance_worktree,
    )

    card = dict(item.operator_decision)
    revision = int(card.get("revision", 1) or 1)
    card.update({
        "status": "resolved",
        "selected_option": option_id,
        "note": note.strip(),
        "resolved_from_revision": revision,
        "revision": revision + 1,
        "continuation_item_id": "",
        "resume_requested": False,
        "resolution_id": f"{card.get('id', decision_id)}:r{revision}",
    })
    if option_id == "decline":
        reply = "The reviewed change was declined. The current runtime is unchanged."
        deployment = {"verdict": "DECLINED"}
        status = "aborted"
        last_error = "operator declined the reviewed framework change"
    else:
        sidecar = (
            Path(mem.project_root)
            / "maintenance"
            / "pending"
            / f"{item.id}.json"
        )
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        approval_binding = metadata["approval_binding"]
        from ..maintenance.deploy_boundary import (
            ReviewedChange,
            approve_reviewed_change,
            deploy_reviewed_change,
        )

        change = ReviewedChange(
            repository=Path(metadata["repository"]),
            public_base=str(metadata["public_base"]),
            reviewed_candidate=str(metadata["reviewed_candidate"]),
            reviewer_verdict=str(metadata["reviewer_verdict"]),
            acceptance_command=tuple(metadata["acceptance_command"]),
            evidence_refs=tuple(metadata["evidence_refs"]),
            mission_id=str(metadata["mission_id"]),
            receipt_dir=Path(metadata["receipt_dir"]),
            origin_remote=str(metadata["origin_remote"]),
            private_remote=str(metadata["private_remote"]),
        )
        approval = approve_reviewed_change(
            change,
            {**card, "input_digest": str(approval_binding["input_digest"])},
        )
        receipt = deploy_reviewed_change(change, approval)
        deployment = {
            "verdict": str(receipt["verdict"]),
            "baseline_failure_count": len(receipt["baseline_failures"]),
            "candidate_failure_count": len(receipt["candidate_failures"]),
            "acceptance_passed": bool(receipt["acceptance_passed"]),
            "release_matches_source": bool(receipt["release_matches_source"]),
            "both_publication_routes_complete": bool(
                receipt["both_publication_routes_complete"]
            ),
            "partial_publication": bool(receipt["partial_publication"]),
            "daemon_roll_permitted": bool(receipt["daemon_roll_permitted"]),
        }
        if deployment["verdict"] == "ADOPT":
            from ..daemon.handoff import request_deployment_handoff

            request_deployment_handoff(
                change.receipt_dir,
                Path(receipt["runtime_source_root"]),
            )
            reply = (
                "The reviewed change passed its checks and both publication "
                "routes completed. The daemon will adopt it at a mission boundary."
            )
            status = "done"
            last_error = ""
        elif deployment["partial_publication"]:
            reply = (
                "Deployment stopped after partial publication. The daemon was not "
                "changed; a fresh deployment run must finish the same reviewed change."
            )
            status = "paused_operator"
            last_error = "reviewed framework change was only partially published"
        else:
            reply = (
                "Deployment rejected the reviewed change. The current runtime and "
                "public main remain unchanged."
            )
            status = "failed"
            last_error = "reviewed framework deployment was rejected"

    resolution_id = card["resolution_id"]
    card["reply"] = reply
    card["deployment"] = deployment
    pending_question = ""
    stored_card = card
    if deployment.get("partial_publication"):
        pending_question = (
            "A publication route remains incomplete. Run a fresh bounded "
            "deployment for the same reviewed change?"
        )
        stored_card = dict(card)
        for key in (
            "continuation_item_id",
            "deployment",
            "reply",
            "resolved_from_revision",
            "resolution_id",
            "resume_requested",
        ):
            stored_card.pop(key, None)
        stored_card.update({
            "id": f"decision-{item.id}-deployment-{revision + 1}",
            "status": "pending",
            "revision": revision + 1,
            "options": [
                option
                for option in card.get("options", ())
                if option.get("id") == "adopt"
            ],
            "superseded_decision_ids": [
                *card.get("superseded_decision_ids", ()),
                str(card.get("id") or decision_id),
            ],
            "question": pending_question,
            "reason": (
                "The reviewed change reached only part of its publication route."
            ),
            "selected_option": "",
            "note": "",
        })
    mem.backlog.update(
        item.id,
        status=status,
        finished_ts=time.time(),
        last_error=last_error,
        pending_question=pending_question,
        operator_decision=stored_card,
    )
    dispose_maintenance_worktree(
        mem.project_root,
        item.id,
        keep_sidecar=bool(deployment.get("partial_publication")),
    )
    JsonlEventSink(None, life_dir=Path(mem.project_root)).append({
        "type": "life.operator_question.answered",
        "item_id": item.id,
        "continuation_item_id": "",
        "question": str(item.pending_question or ""),
        "manager_decision": option_id,
        "decision_id": decision_id,
        "decision_revision": revision,
        "deployment": deployment,
    })
    if pending_question:
        from ..life.supervisor.pending_notify import notify_pending_question

        item.pending_question = pending_question
        item.operator_decision = stored_card
        notify_pending_question(mem.project_root, item)
        JsonlEventSink(None, life_dir=Path(mem.project_root)).append({
            "type": "life.operator_question.pending",
            "item_id": item.id,
            "title": item.title,
            "question": pending_question,
            "agent_layer": "manager",
        })
    return {
        "answered_item_id": item.id,
        "decision_id": decision_id,
        "resolved": True,
        "application_status": "accepted",
        "resolution_id": resolution_id,
        "resume_requested": False,
        "reply": reply,
        "deployment": deployment,
    }


def _apply_operator_answer(
    mem: Any,
    item: Any,
    answer: str,
    *,
    manager_decision: str,
    manager_reply: str,
    decision_option: str = "custom",
    decision_id: str = "",
    decision_note: str = "",
    operator_context_persisted: bool = False,
    operator_context_revision: int = 0,
) -> dict[str, Any]:
    """Persist an explicit operator answer and enqueue its continuation."""
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
        )

    from ..apps._inbox import queue_inbox_message
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    question = str(item.pending_question or "").strip()
    blocked, continuation = mem.backlog.continue_with_operator_reply(
        item.id,
        answer,
        manager_decision=manager_decision,
        decision_option=decision_option,
        decision_id=decision_id,
        decision_note=decision_note,
        manager_reply=manager_reply,
        operator_context_persisted=operator_context_persisted,
    )
    if blocked is None:
        return {"error": "unknown backlog item", "answered_item_id": item.id}
    if continuation is None:
        if decision_id:
            replay = _resolved_decision_replay(
                mem,
                blocked,
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
        f"Blocked item {item.id} was answered and continuation {continuation.id} "
        f"was durably enqueued with this decision: {manager_decision} "
        "Treat this as authority/context and deactivate any stale waiting contract. "
        "Do not enqueue duplicate work if that continuation is already terminal."
    )
    queue_inbox_message(life_dir, directive, source="manager.answer")
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
        "item_id": item.id,
        "continuation_item_id": continuation.id,
        "question": question,
        "manager_decision": manager_decision,
        "decision_id": decision_id,
        "decision_revision": blocked.operator_decision.get(
            "resolved_from_revision"
        ),
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
            blocked.operator_decision.get("resolution_id") or ""
        ),
        "resume_requested": True,
        "reply": manager_reply or "I have delivered your decision to the team.",
        "manager_decision": manager_decision,
        "item": continuation.to_jsonable(),
    }


def _resolve_pending_question_with_manager(
    mem: Any,
    item: Any,
    answer: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    decision_option: str = "custom",
    decision_id: str = "",
    decision_note: str = "",
) -> dict[str, Any]:
    from ..core.operator_context import (
        import_deterministic_credential,
        persist_once_answer,
    )
    from ..manager.front_door import manager_triage
    from ..roles.prompts.manager import build_pending_question_prompt

    root = Path(mem.project_root)
    answer, _credential = import_deterministic_credential(
        root,
        answer,
        global_root=(
            root.parent.parent if root.parent.name == "projects" else None
        ),
    )
    answer_record = persist_once_answer(
        mem.project_root,
        answer,
        source="operator.pending_answer",
        mission_id=str(item.id),
    )
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
        facts = _manager_failure_facts(exc)
        raw_error = f"{type(exc).__name__}: {facts['cause']}"
        failure_kind = (
            f"{facts['phase']}, login_required"
            if facts.get("login_required")
            else facts["phase"]
        )
        message = (
            "Manager pending-question interpretation failed "
            f"[{failure_kind}]: {facts['cause']}. "
            "Your answer is preserved in the inbox/steering record and Manager "
            "interpretation will be retried; the answer was not rejected."
        )
        _emit_pending_question_failure(
            mem,
            item,
            facts,
            error=raw_error,
            answer_preserved=True,
        )
        return {
            "error": message,
            "answered_item_id": item.id,
            "answer_preserved": True,
            **facts,
        }
    parsed = _parse_pending_question_decision(manager_reply or "")
    if parsed is None:
        from ..manager.domain_author import sanitize_model_reply_snippet

        snippet = sanitize_model_reply_snippet(manager_reply or "")
        if snippet:
            phase = "contract"
            cause = (
                "pending_question_decision rejected: IS_ANSWER and RESOLVED "
                "must be booleans, and a resolved answer requires DECISION"
            )
        else:
            phase = "parse"
            cause = "model reply was empty; expected a pending-question decision"
        facts = {
            "phase": phase,
            "cause": cause,
            "contract_field": "pending_question_decision",
            "attempts": 1,
            "model_reply_snippet": snippet,
            "backend_error": "",
        }
        raw_error = "Manager pending-question decision contract failed"
        _emit_pending_question_failure(
            mem,
            item,
            facts,
            error=raw_error,
            answer_preserved=True,
        )
        return {
            "error": f"Manager pending-question interpretation failed [{phase}]: {cause}",
            "answered_item_id": item.id,
            "answer_preserved": True,
            **facts,
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

    return _apply_operator_answer(
        mem,
        item,
        answer,
        manager_decision=parsed["decision"],
        manager_reply=(
            parsed["reply"] or "I have delivered your decision to the team."
        ),
        decision_option=decision_option,
        decision_id=decision_id,
        decision_note=decision_note,
        operator_context_persisted=True,
        operator_context_revision=answer_record.revision,
    )


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
                    mem,
                    item,
                    decision_id=decision_id,
                    option_id=decision_option,
                    note=decision_note,
                )
                if replay is not None:
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
            manager_reply="I delivered your decision to the team.",
            decision_option=decision_option,
            decision_id=decision_id,
            decision_note=decision_note,
            operator_context_persisted=True,
            operator_context_revision=answer_record.revision,
        )
        reply = str(
            result.get("reply")
            or result.get("error")
            or "Manager could not resolve the pending question."
        )
        if result.get("resolved"):
            resumed, projection_error = _reconcile_campaign_after_decision(
                mem,
                stopped=False,
            )
            if resumed:
                result["continuous"] = True
            if projection_error:
                result["projection_error"] = projection_error
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
) -> dict[str, Any] | None:
    """Resolve one visible decision-card option with idempotent replay."""
    from ..core.operator_decision import selected_decision_text
    from ..life.memory import MemoryBundle

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
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
            mem,
            item,
            decision_id=decision_id,
            option_id=option_id,
            note=note,
        )
        if replay is not None:
            if replay.get("application_status") == "already_applied":
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
        if option_id == "stop":
            try:
                operator_text = selected_decision_text(card, option_id, note)
            except ValueError as exc:
                return {"error": str(exc)}
            stopped = mem.backlog.stop_for_operator_decision(
                item.id,
                note=note,
                decision_id=decision_id,
            )
            if stopped is None:
                current = next(
                    (row for row in mem.backlog.history() if row.id == item.id),
                    item,
                )
                replay = _resolved_decision_replay(
                    mem,
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
                "reply": "Campaign stopped. Current work was preserved.",
            }
            if projection_error:
                result["projection_error"] = projection_error
            from ..core.event_catalog import EventType
            from ..core.transcript import append_turn
            from ..life.event_log import JsonlEventSink

            turn_id = f"decision-{decision_id}"
            append_turn(
                mem.project_root,
                "operator",
                operator_text,
                message_id=f"{turn_id}-operator",
            )
            append_turn(
                mem.project_root,
                "argus",
                result["reply"],
                message_id=f"{turn_id}-argus",
            )
            _emit_ui_turn(
                mem.project_root,
                "operator",
                operator_text,
                message_id=f"{turn_id}-operator",
            )
            _emit_ui_turn(
                mem.project_root,
                "argus",
                result["reply"],
                message_id=f"{turn_id}-argus",
            )
            JsonlEventSink(None, life_dir=mem.project_root).append({
                "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
                "item_id": item.id,
                "continuation_item_id": item.id,
                "question": str(item.pending_question or ""),
                "manager_decision": "stop campaign",
                "decision_id": decision_id,
                "decision_revision": stopped.operator_decision.get(
                    "resolved_from_revision"
                ),
                "stopped": True,
            })
            return result
        try:
            answer = selected_decision_text(card, option_id, note)
        except ValueError as exc:
            return {"error": str(exc)}
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
    dispatch_state = result.get("dispatch_state")
    if dispatch_state == "already_queued":
        status = str((result.get("item") or {}).get("status") or "queued")
        text = (
            f"request already queued ({status}); no duplicate task was created"
        )
    elif dispatch_state == "queued_after_current":
        text = (
            "queued after current work; the active executor remains on its "
            "current mission"
        )
    elif dispatch_state == "queued":
        text = "queued; the active executor will pick up this task"
    elif dispatch_state == "running":
        text = "task is running on the active executor"
    elif dispatch_state == "planner_pending":
        text = (
            "campaign updated; the active executor will sequence this objective "
            "through Planner after current work"
            if daemon_alive
            else "campaign updated; executor is starting and Planner will sequence it"
        )
    elif daemon is None and daemon_alive:
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
