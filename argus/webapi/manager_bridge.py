"""Bridge the Web/Ink front-end to the Manager.

Every operator message is one turn of the Manager's own session. It answers
itself, or hands a ticket to the team, asks the operator, or files a setting
or control that Argus applies. The pending-question resolver and the
per-project chat-state/lock bookkeeping live in ``manager_dispatch.py`` /
``manager_pending_question.py`` / ``manager_state.py``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .manager_dispatch import (
    _announce_standing_directive,
    _build_handoff,
    _cancelled_result,
    _dispatch_team_mission,
    _item_to_dict,
    _manager_request_scope,
    _settle_manager_turn,
    _TurnEmitter,
    ask_text,
    record_turn_step,
    tell_team_stage_ruling,
)
from .manager_pending_question import _emit_ui_turn
from .manager_state import (
    _chat_state_for,
    _lock_for,
    manager_control_generation,
)

log = logging.getLogger(__name__)

_PLAN_PREVIEW_CACHE_TTL_S = 60.0


def manager_message(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    on_fragment: Any = None,
    cancelled: Any = None,
    source_channel: str = "web",
    source_message_id: str = "",
    route_override: str = "",
    defer_dispatch_ack: bool = False,
    domain_answer: dict[str, Any] | None = None,
    turn_id: str = "",
    launch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a Manager turn with request-scoped provider interruption."""
    from ..core.run_gateway import run_interrupt_scope

    generation = manager_control_generation(sid)

    def is_cancelled() -> bool:
        if manager_control_generation(sid) != generation:
            return True
        try:
            return callable(cancelled) and bool(cancelled())
        except Exception:  # noqa: BLE001 - retain the existing cancellation callback contract
            return False

    turn_id = turn_id or f"web-{time.time_ns()}"
    draft: dict[str, Any] = {}
    replied = False
    try:
        with run_interrupt_scope(lambda: "operator interrupted Manager request" if is_cancelled() else None):
            result = _manager_message(
                sid, text, global_root=global_root, attachments=attachments,
                on_fragment=on_fragment, cancelled=is_cancelled, source_channel=source_channel,
                source_message_id=source_message_id, route_override=route_override,
                defer_dispatch_ack=defer_dispatch_ack, domain_answer=domain_answer,
                turn_id=turn_id, launch=launch, draft=draft,
            )
        # An error reply replaces the draft like any reply; a cancelled,
        # deferred or crashed turn has none.
        replied = str(result.get("kind") or "") in {"chat", "task", "error"}
        return result
    finally:
        if draft and not replied:
            _close_draft(draft["life_dir"], turn_id)


def _close_draft(life_dir: Path, turn_id: str) -> None:
    """Retire the turn's first words when it ends without a reply (cancelled or failed).

    The closing row shares the draft's message id and, like the draft, is marked
    in progress, so every reader that skips drafts skips it too; clients drop
    the draft it follows instead of leaving it as the last line of the chat.
    """
    _emit_ui_turn(
        life_dir, "argus", "", message_id=f"{turn_id}-argus",
        metadata={"in_progress": True, "draft_closed": True},
    )


def _manager_message(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    on_fragment: Any = None,
    cancelled: Any = None,
    source_channel: str = "web",
    source_message_id: str = "",
    route_override: str = "",
    defer_dispatch_ack: bool = False,
    domain_answer: dict[str, Any] | None = None,
    turn_id: str = "",
    launch: dict[str, Any] | None = None,
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route one operator message through the Manager front-door.

    Returns one of:
      - ``{"kind": "chat", "reply": "<manager reply>"}`` — handled inline (no mission)
      - ``{"kind": "task", "reply": None, "item": {...}, "daemon_alive": bool,
         "daemon_pid": int|None}`` — classified as TEAM work and enqueued
      - ``{"kind": "error", "reply": "<message>"}`` — empty text / triage+enqueue failed

    ``on_fragment(kind, payload)`` — optional streaming callback threaded to
    ``manager_triage``: ``("delta", {...})`` per reply block, ``("phase", {...})``
    per phase transition. ``None`` (the default, used by the blocking POST
    ``/message``) keeps the whole exchange synchronous.

    ``route_override="chat"`` (or ``/ask``) keeps the answer with the Manager:
    it may not hand the work to the team.
    """
    from ..core.launch_settings import launch_hold_context, launch_note, normalize_launch_settings
    from ..core.mission_view import say, uses_cjk
    from ..core.transcript import append_turn
    from ..life.memory import BacklogItem, MemoryBundle
    from ..manager.config_intent import record_manager_handoff
    from ..manager.front_door import manager_session, manager_turn, mission_is_running
    from .attachments import attachment_context_refs, compose_message_body

    resolved_attachments = list(attachments or [])
    # Structured answers are validated under the project lock below. Never
    # interpret their transport text as a command or a different pending task.
    operator_text = "Workflow response" if domain_answer is not None else str(text or "").strip()
    body = compose_message_body(operator_text, resolved_attachments).strip()
    if not body:
        return {"kind": "error", "reply": "empty message"}
    explicit_chat = domain_answer is None and str(route_override or "").strip().lower() == "chat"
    message_attachment_refs = attachment_context_refs(resolved_attachments)

    control_generation = manager_control_generation(sid)
    turn_id = turn_id or f"web-{time.time_ns()}"

    def _cancelled() -> bool:
        if manager_control_generation(sid) != control_generation:
            return True
        if not callable(cancelled):
            return False
        try:
            return bool(cancelled())
        except Exception:  # noqa: BLE001
            return False

    turn_steps: list[dict[str, Any]] = []
    draft_shown: dict[str, Any] = draft if draft is not None else {}

    def _show_draft(payload: dict[str, Any]) -> None:
        # Early words (ux rule 3): the Manager's first message of the turn
        # ("我先看一下 core.py") goes on the shared Activity stream at once,
        # marked in progress. Every client shows it as the start of the reply;
        # the final reply carries the same message id and replaces it. It is
        # never journaled: the transcript keeps only the final reply.
        # A turn that ends without a reply closes it (``_close_draft``).
        if draft_shown or not payload.get("live"):
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        draft_shown["life_dir"] = life_dir
        _emit_ui_turn(
            life_dir, "argus", text, message_id=f"{turn_id}-argus",
            metadata={"in_progress": True, "live": True},
        )

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        # A provider can emit buffered output after its interrupt was accepted.
        # Do not let that old turn reappear in the operator's current stream.
        if _cancelled():
            return
        if kind == "delta":
            _show_draft(payload)
        if kind == "phase":
            changed = record_turn_step(turn_steps, payload)
            if turn_steps:
                emitter.start_task()
            # The Agent activity panel reads persisted work records; the
            # streamed pill alone would leave it empty for a whole chat turn.
            emitter.record_steps(changed)
        if not callable(on_fragment):
            return
        if kind == "delta":
            payload = {
                "fragment_mode": "snapshot",
                **payload,
                "message_id": f"{turn_id}-argus",
            }
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — UI progress must never break a turn
            pass

    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(global_root) if global_root else None
    )
    life_dir = mem.project_root
    credential_record = None
    emitter = _TurnEmitter(
        life_dir=life_dir,
        turn_id=turn_id,
        fragment=_fragment,
        steps=turn_steps,
        task_objective=operator_text,
    )
    if not life_dir.is_dir():
        return {
            "kind": "error",
            "reply": "project no longer exists; the message was not processed",
        }
    # Native domain commands stay on this session and do not run a classifier.
    from ..core.workbench_plugins import native_plugin_command
    with _lock_for(sid):
        if _cancelled():
            return _cancelled_result()
        plugin_reply = native_plugin_command(operator_text, sid=sid,
            life_dir=life_dir, global_root=mem.global_root)
        if plugin_reply is not None:
            append_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")
            append_turn(life_dir, "argus", plugin_reply)
            _emit_ui_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")
            _emit_ui_turn(life_dir, "argus", plugin_reply, message_id=f"{turn_id}-argus")
            _fragment("delta", {"text": plugin_reply})
            return {"kind": "chat", "reply": plugin_reply}

    # Atlas card references: replace each ``[[Argus引用 {...}]]`` marker line
    # with a readable inline line in the persisted operator text, and carry the
    # bounded context block separately so only the model-facing bodies (triage
    # send_body / TEAM routing body) receive it — never the journal.
    reference_context = ""
    reference_deps: list[str] = []
    reference_ids: list[str] = []
    from .map_references import REFERENCE_MARKER_PREFIX, expand_operator_references

    if REFERENCE_MARKER_PREFIX in operator_text:
        try:
            expansion = expand_operator_references(operator_text, life_dir)
        except Exception:  # noqa: BLE001 — a quoted card must never block the turn
            log.exception("map reference expansion failed")
            expansion = None
        if expansion is not None and expansion.matched:
            operator_text = expansion.text
            body = compose_message_body(operator_text, resolved_attachments).strip()
            reference_context = expansion.context_block
            reference_deps = list(expansion.dep_task_ids)
            # The cards the operator asked about: the map links this turn,
            # and any task it hands to the team, back to them.
            reference_ids = list(dict.fromkeys(ref.task_id for ref in expansion.references))

    from ..core.operator_context import import_deterministic_credential

    safe_body, credential_record = import_deterministic_credential(
        life_dir,
        body,
        global_root=mem.global_root,
    )
    if safe_body != body:
        body = safe_body
        operator_text, _separator, _attachments = safe_body.partition("\n\nAttachments:\n")
    emitter.task_objective = operator_text

    from ..manager.ask_intent import strip_ask_prefix

    if domain_answer is None:
        # A readable title before any model call: the sidebar must never fall
        # back to the raw session id while a topic summary is pending/denied.
        # Seeded only after credentials are redacted and Atlas references are
        # expanded, from the question itself for `/ask`.
        from ..core.session import seed_provisional_session_name

        seed_provisional_session_name(
            mem.global_root, sid, strip_ask_prefix(operator_text) or operator_text
        )

    # `/ask` keeps the answer with the Manager: no team.
    question = strip_ask_prefix(operator_text)
    if question is not None:
        operator_text, explicit_chat = question, True
        body = compose_message_body(operator_text, resolved_attachments).strip()

    lock = _lock_for(sid)
    with lock:
        if _cancelled():
            return _cancelled_result()
        if not life_dir.is_dir():
            return {
                "kind": "error",
                "reply": "project no longer exists; the message was not processed",
            }
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        active_mission = mission_is_running(mem)
        from ..manager.domain_intake import intake_answer, intake_card, read_intake, write_intake

        choice_action = ""
        if domain_answer is not None:
            try:
                if active_mission:
                    raise ValueError("当前任务仍在运行，请完成或停止后再选择流程。 / Finish or stop the active task before choosing a workflow.")
                operator_text, choice_action = intake_answer(read_intake(life_dir), domain_answer)
            except ValueError as exc:
                return {"kind": "error", "resolved": False, "reply": str(exc)}
            body = operator_text
            emitter.task_objective = operator_text
        # Build the restart handoff before journaling this turn so the current
        # message appears exactly once.
        startup_handoff = ""
        # A project without a Manager session yet (a new one, or one whose last
        # turn failed) starts it from the conversation so far.
        if manager_session(mem) is None:
            try:
                transcript = Path(life_dir) / "transcript.jsonl"
                if transcript.exists() and transcript.stat().st_size > 0:
                    startup_handoff = _build_handoff(life_dir)
            except Exception:  # noqa: BLE001 — continuity is best-effort
                pass

        # Journal the operator turn (transcript.jsonl role=operator) for
        # resume/replay. Best-effort — never block the reply.
        try:
            append_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")
        except Exception:  # noqa: BLE001
            pass
        _emit_ui_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator",
                      metadata={"refs": reference_ids} if reference_ids else None)

        if _cancelled():
            return _cancelled_result()

        if reference_context:
            body = f"{body}\n\n{reference_context}"
        # Launch settings reach the Manager as operator text with the request;
        # the journaled operator turn above stays exactly what was typed.
        launch_settings = normalize_launch_settings(launch)
        body += launch_note(launch_settings, language_hint=operator_text)
        root_task_id = BacklogItem.new_id()
        public_task = operator_text
        intake_before = None
        handoff = None
        reply = None
        if choice_action:
            from ..manager.domain_intake import handle_intake, manager_intake_decision
            from ..skills.vertical_select import available_verticals
            from ..verticals import list_all_data_domain_names

            intake_before = read_intake(life_dir)
            domain_decision: dict[str, Any] = {"action": choice_action}
            try:
                if choice_action in {"ask", "prepare", "dialogue"}:
                    emitter.phase(say("gathering_options", uses_cjk(operator_text)))
                    domain_decision = manager_intake_decision(
                        mem, chat_state, operator_text, root_task_id=root_task_id, on_fragment=emitter.fragment,
                    )
                    if _cancelled():
                        return _cancelled_result()
                intake = handle_intake(life_dir, operator_text, domain_decision, route="simple",
                                       self_mode=intake_before.get("self_mode") or "inspect",
                                       known_verticals=tuple(available_verticals()) + tuple(list_all_data_domain_names(life_dir)))
            except Exception as exc:  # noqa: BLE001 — retain the user's card and answer on provider/format failure
                if _cancelled():
                    return _cancelled_result()
                log.warning("Manager workflow dialogue failed: %s", type(exc).__name__)
                return emitter.respond(
                    say("options_failed", uses_cjk(operator_text)), {"kind": "error", "resolved": False},
                )
            if intake is not None and "reply" in intake:
                return emitter.respond(intake["reply"], {"kind": "chat", "decision_card": intake_card(read_intake(life_dir))})
            if intake is not None:
                body = intake["task"]
                public_task = emitter.task_objective = intake["objective"]
                if intake["route"] == "complex":
                    chat_state["_team_lifetime"] = "bounded"
                    chat_state["_approved_domain_decision"] = intake["decision"]
                    chat_state["_domain_display_objective"] = {
                        "execution_task": body, "objective": intake["display_objective"],
                        "title": intake["title"],
                    }
                    handoff = intake
        chat_state["_domain_setup_enabled"] = not active_mission and not explicit_chat
        if handoff is None:
            # One message, one turn of the Manager's own session.
            emitter.solo = True
            emitter.start_task()
            emitter.phase(say("working_out_reply", uses_cjk(operator_text)))
            turn_input = f"{startup_handoff}\n\n{body}" if startup_handoff else body
            # What a person placed on the canvas: context for this turn only,
            # never part of a ticket the team receives.
            if canvas_context := manager_canvas_context(life_dir):
                turn_input = f"{turn_input}\n\n{canvas_context}"
            # The hold before Start is Argus's state, told beside the request.
            if launch_settings and launch_settings.get("hold"):
                turn_input = f"{turn_input}\n\n{launch_hold_context(operator_text)}"
            reply, handoff = manager_turn(
                mem, turn_input, chat_state,
                on_fragment=emitter.fragment, root_task_id=root_task_id, team_allowed=not explicit_chat,
            )
            tell_team_stage_ruling(life_dir, chat_state)
            if _cancelled():
                emitter.cancel_task()
                return _cancelled_result()
            if reply is None:
                return emitter.respond(say("manager_unavailable", uses_cjk(operator_text)), {"kind": "error"})
            if handoff is not None:
                record_manager_handoff(mem, operator_text, chat_state, handoff, root_task_id=root_task_id,
                                       credential_imported=credential_record is not None)
                _announce_standing_directive(chat_state, life_dir, turn_id)
            settled = _settle_manager_turn(
                mem, reply, handoff, chat_state, emitter, source_channel=source_channel,
                source_message_id=source_message_id, operator_text=operator_text,
            )
            if settled is not None:
                return settled
            # The ticket carries its route, checked in the Manager's turn.
            chat_state["_approved_domain_decision"] = handoff.value
            body = handoff.value.execution_task
            public_task = handoff.get("goal")
            chat_state["_team_lifetime"] = handoff.get("lifetime", "bounded")

        # 2) The Manager handed a ticket to the team.
        #
        # If the project lifecycle is ``done``, auto-resume it so the new
        # work can actually be picked up by the daemon.  Quarantined/archived
        # projects raise RuntimeError which is caught below and returned as a
        # structured ``{"kind": "error"}`` response — never a bare HTTP 500.
        if _cancelled():
            if intake_before:
                write_intake(life_dir, intake_before)
            return _cancelled_result()
        try:
            item, daemon_alive, daemon_pid = _dispatch_team_mission(
                mem,
                body,
                chat_state,
                root_task_id,
                _cancelled,
                emitter,
                attachment_context_refs=message_attachment_refs,
                reference_deps=reference_deps,
                public_objective=public_task,
            )
        except Exception as exc:  # noqa: BLE001
            from ..manager.dispatch import MissionPersistenceError

            if intake_before and not isinstance(exc, MissionPersistenceError):
                write_intake(life_dir, intake_before)
            if _cancelled():
                return _cancelled_result()
            from ..manager.domain_intake import DomainOfferRequired

            if isinstance(exc, DomainOfferRequired):
                return emitter.respond(str(exc), {"kind": "chat", "decision_card": intake_card(read_intake(life_dir))})
            log.warning("Manager could not safely prepare operator work: %s", exc)
            if isinstance(exc, MissionPersistenceError):
                pending_item = chat_state["_pending_missions"][-1][0]
                return emitter.respond(
                    str(exc),
                    {
                        "kind": "error",
                        "item": _item_to_dict(pending_item, operator_text or body),
                    },
                )
            error_reply = (
                "I couldn't safely prepare that request, so nothing was queued "
                "or executed. Clarify the target and allowed scope, or retry later."
            )
            return emitter.respond(error_reply, {"kind": "error"})

    if _cancelled():
        return _cancelled_result()

    item_payload = _item_to_dict(item, public_task or body)
    result = {
        "kind": "task",
        # The Manager's own words on the ticket it handed over, if it wrote them.
        "reply": reply,
        "item": item_payload,
        "daemon_alive": bool(daemon_alive),
        "daemon_pid": daemon_pid,
        "continuous": bool(chat_state.get("config", {}).get("continuous")),
    }
    if item_payload is None and result["continuous"]:
        result["dispatch_state"] = "planner_pending"
    elif item_payload is not None and daemon_alive:
        running_id = next(
            (
                str(row.id)
                for row in mem.backlog.active()
                if str(row.status) == "running"
            ),
            "",
        )
        if running_id == str(item_payload.get("id") or ""):
            result["dispatch_state"] = "running"
        elif running_id:
            result["dispatch_state"] = "queued_after_current"
        else:
            result["dispatch_state"] = "queued"
    title = str(
        (item_payload or {}).get("title")
        or (item_payload or {}).get("objective")
        or operator_text
        or body
    )
    if defer_dispatch_ack:
        emitter.phase(
            "已保存，正在确认执行状态" if uses_cjk(text)
            else "Saved; checking executor status"
        )
    elif result.get("dispatch_state") == "planner_pending":
        emitter.emit_only(
            "Campaign updated · Planner will sequence this objective after "
            f"current work · {title}"
        )
    else:
        emitter.emit_only(f"Queued · {title}")
    return result


def manager_canvas_context(life_dir: Path) -> str:
    """What a person placed on the canvas, as context for one Manager turn (web chat, chat router, team reports)."""
    from .canvas import canvas_context_block
    from .map_references import _backlog_by_id

    def titles() -> dict[str, str]:
        return {task_id: str(getattr(item, "title", "") or "") for task_id, item in _backlog_by_id(life_dir).items()}

    return canvas_context_block(life_dir, titles=titles)


def manager_team_report(sid: str, text: str, *, message_id: str, event_fields: dict[str, Any],
                        global_root: Path | str) -> dict[str, Any]:
    """Answer a team report as a turn of the Manager's own session.

    The operator reads the Manager's words under the report's message id and
    card fields; if the Manager cannot answer, they read the team's report.
    """
    from ..core.mission_view import uses_cjk
    from ..core.operator_messages import publish_operator_message
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_turn

    mem = MemoryBundle.for_cwd(fingerprint=sid, global_root=Path(global_root))
    with _lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state.update(session_id=sid, global_root=str(mem.global_root))
        turn_input = f"Team report:\n{text}"
        # The canvas is context for this decision too, as in the web chat.
        if canvas_context := manager_canvas_context(Path(mem.project_root)):
            turn_input = f"{turn_input}\n\n{canvas_context}"
        reply, handoff = manager_turn(mem, turn_input, chat_state, team_allowed=False)
        tell_team_stage_ruling(mem.project_root, chat_state)
        if reply is None or chat_state.pop("_self_failure", None):
            reply = text
        elif handoff is not None and handoff.to == "operator":
            from ..manager._stage_ops import park_stage_question_on_task

            reply = ask_text(handoff, uses_cjk(reply))
            # The stage question's task waits on the operator's answer, on its card.
            park_stage_question_on_task(mem.project_root, handoff)
        publish_operator_message(mem.project_root, text=reply, message_id=message_id, event_fields=event_fields)
    return {"kind": "chat", "reply": reply}


def manager_plan(
    sid: str, text: str, *, global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Keep plan previews interruptible while they own the Manager session."""
    with _manager_request_scope(sid):
        return _manager_plan(sid, text, global_root=global_root)


def _manager_plan(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Draft one bounded execution plan through the configured Planner role."""
    from ..core.knobs import resolve_light_effort, resolve_light_model
    from ..life.memory import MemoryBundle
    from ..manager.front_door import _ensure_manager_runner
    from ..manager.plan_mode import draft_plan

    body = (text or "").strip()
    if not body:
        return {"steps": [], "notes": [], "error": "empty objective"}
    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(global_root) if global_root else None
    )
    with _lock_for(sid):
        if not mem.project_root.is_dir():
            return {
                "steps": [],
                "notes": [],
                "error": "project no longer exists",
            }
        state = _chat_state_for(sid)
        runner = _ensure_manager_runner(state, mem)
        backend = getattr(runner, "planner_backend", None) if runner is not None else None
        model, effort = resolve_light_model(), resolve_light_effort()
        cache_key = (body, model, effort, id(backend))
        cached = state.get("plan_preview_cache")
        if (
            isinstance(cached, tuple)
            and len(cached) == 3
            and cached[0] == cache_key
            and time.monotonic() - float(cached[1]) < _PLAN_PREVIEW_CACHE_TTL_S
        ):
            return dict(cached[2])
        plan = draft_plan(
            backend,
            body,
            model=model,
            reasoning_effort=effort,
            run_label="planner-preview",
        )
        result = {
            "steps": [
                {"title": step.title, "detail": step.detail}
                for step in plan.steps
            ],
            "notes": list(plan.notes),
            "error": plan.error,
        }
        if not plan.error:
            state["plan_preview_cache"] = (
                cache_key,
                time.monotonic(),
                result,
            )
    return result


def _rewrite_project_context(mem: Any, sid: str) -> str:
    """Advisory context for a prompt rewrite: what project is the operator in?

    Purely factual and best-effort — the workdir, the standing objective, and
    the resolved vertical. It exists so the Manager does not have to ask the
    operator questions the session already answers. It is NOT a judgment about
    what the operator wants, and an empty string is a fine result.
    """
    lines: list[str] = []
    try:
        from ..core.session import read_session_meta

        meta = read_session_meta(getattr(mem, "global_root", None), sid)
        if meta is not None:
            workdir = (meta.workdir or meta.cwd or "").strip()
            if workdir:
                try:
                    from ..core.campaign_workdir import active_campaign_workdir

                    active = active_campaign_workdir(mem.root, workdir)
                except Exception:  # noqa: BLE001 - advisory context only
                    active = None
                lines.append(f"- working directory: {active or workdir}")
                if active is not None:
                    lines.append(f"- session workspace: {workdir}")
            if (meta.display_name or "").strip():
                lines.append(f"- session: {meta.display_name.strip()}")
            if (meta.objective or "").strip():
                lines.append(f"- standing objective: {meta.objective.strip()[:400]}")
    except Exception:  # noqa: BLE001 — context is advisory
        pass
    try:
        from ..skills.vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(mem.project_root)
        if vertical:
            lines.append(f"- active workflow (vertical): {vertical}")
    except Exception:  # noqa: BLE001 — context is advisory
        pass
    return "\n".join(lines)


def manager_rewrite(
    sid: str, text: str, *, global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Keep prompt previews interruptible while they own the Manager session."""
    with _manager_request_scope(sid):
        return _manager_rewrite(sid, text, global_root=global_root)


def _manager_rewrite(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Restate a short operator draft as an executable brief, via the Manager.

    A preview only: the result is handed back to the operator to accept, edit,
    or discard. Nothing is enqueued and no mission is touched. On failure the
    caller keeps the operator's original text — see
    :func:`argus.manager.prompt_rewrite.rewrite_prompt`.
    """
    from ..life.memory import MemoryBundle
    from ..manager.front_door import _ensure_manager_runner
    from ..manager.prompt_rewrite import rewrite_prompt

    body = (text or "").strip()
    if not body:
        return {
            "original": "",
            "rewritten": "",
            "changes": [],
            "questions": [],
            "error": "empty prompt",
        }
    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(global_root) if global_root else None
    )
    with _lock_for(sid):
        if not mem.project_root.is_dir():
            return {
                "original": body,
                "rewritten": "",
                "changes": [],
                "questions": [],
                "error": "project no longer exists",
            }
        state = _chat_state_for(sid)
        runner = _ensure_manager_runner(state, mem)
        from ..core.knobs import resolve_light_effort, resolve_light_model
        from ..core.operator_context import build_operator_context_block

        model, effort = resolve_light_model(), resolve_light_effort()

        operator_context, operator_context_revision = build_operator_context_block(
            "manager", mem.project_root, consume_once=False
        )
        project_context = _rewrite_project_context(mem, sid)
        rewrite = rewrite_prompt(
            runner,
            body,
            model=model,
            reasoning_effort=effort,
            run_label="manager-rewrite",
            project_context=project_context,
            operator_context=operator_context,
        )
    return {
        "original": rewrite.original or body,
        "rewritten": rewrite.rewritten,
        "changes": list(rewrite.changes),
        "questions": list(rewrite.questions),
        "error": rewrite.error,
        "operator_context_revision": operator_context_revision,
    }
