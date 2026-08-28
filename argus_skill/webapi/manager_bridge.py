"""Bridge the Web/Ink front-end to the Manager routing pipeline.

An operator message is NOT blindly turned into a backlog task. It goes through
``manager_triage`` — chat-vs-task classification + an inline reply for chat/SELF
work. A conversational "你好" gets a Manager reply and never touches the daemon
or a vertical; only TEAM/complex work is enqueued as a mission.

The classify/control/config/SELF/TEAM phase helpers, the pending-question
resolver, and the per-project chat-state/lock bookkeeping live in
``manager_dispatch.py`` / ``manager_pending_question.py`` / ``manager_state.py``
respectively; this module keeps only the top-level ``manager_message``,
``manager_plan``, and prompt-rewrite request pipeline. Callers import state,
dispatch, and pending-question operations from their owning modules.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .manager_dispatch import (
    _build_handoff,
    _cancelled_result,
    _classify_operator_turn,
    _dispatch_team_mission,
    _handle_abort_control,
    _handle_authorization_control,
    _handle_pause_control,
    _handle_pending_question_turn,
    _handle_steer_control,
    _item_to_dict,
    _maybe_apply_config_intent,
    _maybe_greeting_reply,
    _run_triage_and_fallbacks,
    _TurnEmitter,
)
from .manager_pending_question import _emit_ui_turn
from .manager_session_intent import contextualize_operator_turn
from .manager_state import (
    _chat_state_for,
    _lock_for,
    interrupt_manager_turns,
    manager_control_generation,
)

log = logging.getLogger(__name__)

_PLAN_PREVIEW_CACHE_TTL_S = 60.0
_TEAM_REPLAY_WINDOW_S = 90.0


def _recent_team_replay(
    mem: Any,
    body: str,
    prior_turns: list[dict[str, Any]],
) -> Any | None:
    request = " ".join(str(body or "").split())
    eligible_statuses = {"pending", "running", "done", "paused_operator"}
    recent_items = []
    now = time.time()
    for item in sorted(mem.backlog.history(), key=lambda row: float(row.ts), reverse=True):
        if now - float(item.ts) > _TEAM_REPLAY_WINDOW_S:
            break
        if str(item.status) not in eligible_statuses:
            continue
        recent_items.append(item)
        prior = " ".join(
            str(item.original_objective or item.objective or "").split()
        )
        if prior == request:
            return item

    previous_operator = next(
        (
            turn
            for turn in reversed(prior_turns)
            if str(turn.get("role") or "") == "operator"
        ),
        None,
    )
    if previous_operator is None:
        return None
    previous_request = " ".join(str(previous_operator.get("text") or "").split())
    previous_ts = float(previous_operator.get("ts") or 0.0)
    if previous_request != request or previous_ts <= 0:
        return None
    for item in recent_items:
        if float(item.ts) >= previous_ts:
            return item
    return None


def _answer_inline(sid: str, life_dir: Any, question: str) -> str:
    """Answer *question* with the Manager alone — no classify, no backlog.

    Uses the same front-door runner the classifier would have used, which
    exists precisely to "reply in-band BEFORE anything reaches the backlog".
    Any failure returns a plain message rather than falling through to task
    dispatch: the operator said this was a question, and quietly turning it
    into queued work is the behaviour `/ask` exists to prevent.
    """
    from ..core.models import RunnerOptions
    from ..core.run_gateway import run_exec as gateway_run_exec
    from ..life.memory import LifeMemory
    from ..manager.front_door import _ensure_manager_runner
    from ..roles.prompts.manager import build_quick_reply_prompt

    try:
        mem = LifeMemory.open(Path(str(life_dir)))
        chat_state = _chat_state_for(sid)
        runner = _ensure_manager_runner(chat_state, mem)
        if runner is None:
            return (
                "No conversational backend is available for this project, so "
                "`/ask` cannot answer inline. Send the message without `/ask` "
                "to queue it as work instead."
            )
        from ..core.operator_context import (
            append_operator_context,
            build_operator_context_block,
        )

        operator_context, _revision = build_operator_context_block(
            "manager", life_dir, consume_once=False
        )
        prompt = build_quick_reply_prompt(objective=question)
        prompt = append_operator_context(prompt, operator_context)
        result = gateway_run_exec(
            chat_state.get("manager_session") or runner,
            prompt=prompt,
            options=RunnerOptions(skip_git_repo_check=True),
            run_label="manager-ask",
        )
    except Exception:  # noqa: BLE001 - never turn a question into a task
        log.exception("ask: inline reply failed")
        return "Could not answer inline just now; nothing was queued."

    reply = str(getattr(result, "stdout", "") or "").strip()
    return reply or "The Manager returned an empty reply; nothing was queued."


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

    The pipeline below is a sequence of typed phase helpers (pending-question,
    classify, greeting shortcut, authorization/steer/pause/abort control, config
    intent, triage+fallbacks, TEAM dispatch) — each either returns a terminal
    result or ``None``/a small typed result to let the next phase run. This
    mirrors the original single-function control flow exactly; only the
    boilerplate (streaming/journaling a reply) is now shared via
    ``_TurnEmitter``.
    """
    from ..core.transcript import append_turn
    from ..life.memory import MemoryBundle
    from ..manager.front_door import mission_is_running
    from .attachments import attachment_context_refs, compose_message_body

    resolved_attachments = list(attachments or [])
    operator_text = str(text or "").strip()
    body = compose_message_body(operator_text, resolved_attachments).strip()
    if not body:
        return {"kind": "error", "reply": "empty message"}
    message_attachment_refs = attachment_context_refs(resolved_attachments)

    control_generation = manager_control_generation(sid)
    turn_id = f"web-{time.time_ns()}"

    def _cancelled() -> bool:
        if manager_control_generation(sid) != control_generation:
            return True
        if not callable(cancelled):
            return False
        try:
            return bool(cancelled())
        except Exception:  # noqa: BLE001
            return False

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
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

    def _after_reply(reply: str) -> None:
        runner = _chat_state_for(sid).get("manager_runner")
        schedule = getattr(runner, "_schedule_self_learning_review", None)
        if not callable(schedule):
            return
        try:
            schedule(objective=operator_text, reply=reply)
        except Exception as exc:  # noqa: BLE001 - learning never owns the answer
            from ..life.event_log import JsonlEventSink

            JsonlEventSink(None, life_dir=life_dir).append({
                "type": "self.learning.review.failed",
                "agent_layer": "self",
                "error": f"{type(exc).__name__}: {exc}",
            })

    emitter = _TurnEmitter(
        life_dir=life_dir,
        turn_id=turn_id,
        fragment=_fragment,
        after_reply=_after_reply,
    )
    if not life_dir.is_dir():
        return {
            "kind": "error",
            "reply": "project no longer exists; the message was not processed",
        }

    from ..core.operator_context import import_deterministic_credential

    safe_body, credential_record = import_deterministic_credential(
        life_dir,
        body,
        global_root=mem.global_root,
    )
    if safe_body != body:
        body = safe_body
        operator_text, _separator, _attachments = safe_body.partition("\n\nAttachments:\n")

    from ..manager.ask_intent import strip_ask_prefix

    # `/ask` fixes the route, but still uses the shared intake classification
    # before the separate Manager answer turn.
    _question = strip_ask_prefix(operator_text)
    if _question is not None:
        with _lock_for(sid):
            from ..manager.config_intent import _front_door_classify

            chat_state = _chat_state_for(sid)
            chat_state["session_id"] = sid
            chat_state["global_root"] = str(mem.global_root)
            chat_state["_frontdoor_credential_imported"] = (
                credential_record is not None
            )
            _front_door_classify(
                mem,
                body,
                chat_state,
                active_mission=mission_is_running(mem),
            )
        try:
            append_turn(life_dir, "operator", body)
        except Exception:  # noqa: BLE001
            pass
        _emit_ui_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")
        reply = _answer_inline(
            sid,
            life_dir,
            compose_message_body(_question, resolved_attachments),
        )
        return emitter.respond(reply, {"kind": "chat"})

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
        prior_turns: list[dict[str, Any]] = []
        try:
            from ..core.transcript import read_turns

            prior_turns = read_turns(life_dir, limit=6)
        except Exception:  # noqa: BLE001 - bounded context is an optimization
            pass

        # Build the restart handoff before journaling this turn so the current
        # message appears exactly once. Do not consume it until a model-backed
        # path actually needs it.
        startup_handoff = ""
        startup_handoff_pending = bool(chat_state.get("needs_startup_handoff", False))
        if startup_handoff_pending:
            try:
                transcript = Path(life_dir) / "transcript.jsonl"
                if transcript.exists() and transcript.stat().st_size > 0:
                    startup_handoff = _build_handoff(life_dir)
            except Exception:  # noqa: BLE001 — continuity is best-effort
                pass

        # Journal the operator turn (transcript.jsonl role=operator) for
        # resume/replay. Best-effort — never block the reply.
        try:
            append_turn(life_dir, "operator", body)
        except Exception:  # noqa: BLE001
            pass
        _emit_ui_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")

        duplicate_item = _recent_team_replay(mem, body, prior_turns)
        if duplicate_item is not None:
            from ..manager.dispatch import _daemon_status

            daemon_alive, daemon_pid = _daemon_status(life_dir)
            item_payload = _item_to_dict(duplicate_item, operator_text or body)
            title = str(
                item_payload.get("title")
                or item_payload.get("objective")
                or operator_text
                or body
            )
            emitter.emit_only(f"Already queued · {title}")
            return {
                "kind": "task",
                "reply": None,
                "item": item_payload,
                "daemon_alive": daemon_alive,
                "daemon_pid": daemon_pid,
                "continuous": bool(
                    chat_state.get("config", {}).get("continuous")
                ),
                "dispatch_state": "already_queued",
                "duplicate": True,
            }

        pending_questions = [
            item
            for item in mem.backlog.active()
            if item.pending_question.strip()
        ]
        pending_result = _handle_pending_question_turn(
            mem, pending_questions, body, chat_state, emitter
        )
        if _cancelled():
            return _cancelled_result()
        if pending_result is not None:
            return pending_result

        previous_items = mem.backlog.history()
        last_team_task = ""
        if previous_items:
            previous = max(previous_items, key=lambda item: float(item.ts))
            last_team_task = str(
                previous.original_objective or previous.objective or ""
            )
        routing_body = compose_message_body(
            contextualize_operator_turn(
                operator_text,
                prior_turns,
                last_team_task=last_team_task,
            ),
            resolved_attachments,
        ).strip()
        chat_state["_frontdoor_contextual_text"] = body
        chat_state["_frontdoor_dispatch_body"] = routing_body

        # A web-process restart necessarily loses the live ACP process. Resume
        # seamlessly by opening one new warm conversation session with a
        # structured handoff built from the transcript that existed BEFORE this
        # operator turn. A deterministic status read must not consume this seam.
        if startup_handoff_pending:
            chat_state.pop("needs_startup_handoff", None)
            if startup_handoff:
                chat_state["startup_handoffs"] = int(
                    chat_state.get("startup_handoffs", 0)
                ) + 1

        chat_state["_frontdoor_credential_imported"] = credential_record is not None
        classify = _classify_operator_turn(
            mem,
            body,
            chat_state,
            active_mission,
            life_dir,
            startup_handoff,
            emitter,
            _cancelled,
        )
        if isinstance(classify, dict):
            return classify
        intent, control, route = classify.intent, classify.control, classify.route
        send_body, root_task_id = classify.send_body, classify.root_task_id
        frontdoor_failure = classify.frontdoor_failure

        greeting_result = _maybe_greeting_reply(classify, body, emitter)
        if greeting_result is not None:
            return greeting_result

        authorization_actions = chat_state.pop(
            "_frontdoor_authorization",
            None,
        )
        if isinstance(authorization_actions, list) and authorization_actions:
            if _cancelled():
                return _cancelled_result()
            return _handle_authorization_control(
                chat_state,
                life_dir,
                authorization_actions,
                source_channel,
                source_message_id,
                turn_id,
                emitter,
            )

        if control == "steer":
            if _cancelled():
                return _cancelled_result()
            return _handle_steer_control(chat_state, life_dir, emitter)

        if control == "pause":
            if _cancelled():
                return _cancelled_result()
            interrupt_manager_turns(sid)
            return _handle_pause_control(operator_text, chat_state, life_dir, emitter)

        if control == "no_dispatch":
            route = "simple"

        if control == "abort":
            if _cancelled():
                return _cancelled_result()
            return _handle_abort_control(operator_text, life_dir, emitter)

        config_result = _maybe_apply_config_intent(
            mem,
            intent,
            chat_state,
            life_dir,
            turn_id,
            on_fragment,
            _fragment,
            _cancelled,
        )
        if config_result is not None:
            if _cancelled():
                return _cancelled_result()
            return config_result

        triage_result = _run_triage_and_fallbacks(
            mem,
            send_body,
            chat_state,
            route,
            control,
            root_task_id,
            frontdoor_failure,
            on_fragment,
            emitter,
        )
        if triage_result is not None:
            if _cancelled():
                return _cancelled_result()
            return triage_result

        # 2) TEAM/complex — apply the BOUNDED/STANDING lifetime selected by the
        # same merged front-door call. Chat and SELF work already returned above.
        #
        # If the project lifecycle is ``done``, auto-resume it so the new
        # work can actually be picked up by the daemon.  Quarantined/archived
        # projects raise RuntimeError which is caught below and returned as a
        # structured ``{"kind": "error"}`` response — never a bare HTTP 500.
        if _cancelled():
            return _cancelled_result()
        try:
            item, daemon_alive, daemon_pid = _dispatch_team_mission(
                mem,
                routing_body,
                chat_state,
                root_task_id,
                _cancelled,
                emitter,
                attachment_context_refs=message_attachment_refs,
            )
        except Exception as exc:  # noqa: BLE001
            if _cancelled():
                return _cancelled_result()
            log.warning("Manager could not safely prepare operator work: %s", exc)
            from ..manager.dispatch import MissionPersistenceError
            from ..manager.front_door import ManagerModelCapabilityMismatchError

            if isinstance(exc, MissionPersistenceError):
                pending_item = chat_state["_pending_missions"][-1][0]
                return emitter.respond(
                    str(exc),
                    {
                        "kind": "error",
                        "item": _item_to_dict(pending_item, operator_text or body),
                    },
                )
            if isinstance(exc, ManagerModelCapabilityMismatchError):
                return emitter.respond(str(exc), {"kind": "error"})
            error_reply = (
                "I couldn't safely prepare that request, so nothing was queued "
                "or executed. Clarify the target and allowed scope, or retry later."
            )
            return emitter.respond(error_reply, {"kind": "error"})

    if _cancelled():
        return _cancelled_result()

    item_payload = _item_to_dict(item, operator_text or body)
    result = {
        "kind": "task",
        "reply": None,
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
    if result.get("dispatch_state") == "planner_pending":
        emitter.emit_only(
            "Campaign updated · Planner will sequence this objective after "
            f"current work · {title}"
        )
    else:
        emitter.emit_only(f"Queued · {title}")
    return result


def manager_plan(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Draft one bounded execution plan through the configured Planner role."""
    from ..core.knobs import resolve_role_reasoning_effort
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
        model = _plan_preview_model()
        effort = resolve_role_reasoning_effort(
            "ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT",
            default="low",
        )
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


def _plan_preview_model() -> str:
    """Resolve the interactive ``/plan`` preview route.

    The preview is a fast sketch shown while the operator is still typing, so
    it wants a compact model — but only a backend that actually serves the
    OpenAI catalog can be handed an OpenAI id. See
    ``core.knobs.resolve_cheap_route_model``.
    """
    from ..core.knobs import resolve_cheap_route_model

    return resolve_cheap_route_model(
        knob="ARGUS_SKILL_PLAN_PREVIEW_MODEL",
        catalog_default="gpt-5.4-mini",
        role="planner",
        role_env="ARGUS_SKILL_PLAN_MODEL",
    )


def _rewrite_model_and_effort() -> tuple[str, str]:
    """Resolve the interactive prompt-rewrite route independently of Manager chat."""
    from ..core.knobs import (
        resolve_cheap_route_model,
        resolve_role_reasoning_effort,
    )

    model = resolve_cheap_route_model(
        knob="ARGUS_SKILL_REWRITE_MODEL",
        # Rewrite has always used the full mid-tier id here rather than the
        # mini the other three cheap routes take; keep that on the backends
        # where it resolves, and fall back to the Manager model elsewhere.
        catalog_default="gpt-5.5",
        role="manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
    )
    effort = resolve_role_reasoning_effort(
        "ARGUS_SKILL_REWRITE_REASONING_EFFORT",
        default="high",
    )
    return model, effort


def manager_rewrite(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Restate a short operator draft as an executable brief, via the Manager.

    A preview only: the result is handed back to the operator to accept, edit,
    or discard. Nothing is enqueued and no mission is touched. On failure the
    caller keeps the operator's original text — see
    :func:`argus_skill.manager.prompt_rewrite.rewrite_prompt`.
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
        model, effort = _rewrite_model_and_effort()
        from ..core.operator_context import build_operator_context_block

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
