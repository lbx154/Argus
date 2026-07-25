"""Bridge the Web/Ink front-end to the Manager routing pipeline.

An operator message is NOT blindly turned into a backlog task. It goes through
``manager_triage`` — chat-vs-task classification + an inline reply for chat/SELF
work. A conversational "你好" gets a Manager reply and never touches the daemon
or a vertical; only TEAM/complex work is enqueued as a mission.

The classify/control/config/SELF/TEAM phase helpers, the pending-question
resolver, and the per-project chat-state/lock bookkeeping live in
``manager_dispatch.py`` / ``manager_pending_question.py`` / ``manager_state.py``
respectively (extracted as part of a behavior-preserving decomposition); this
module keeps only the top-level ``manager_message`` / ``manager_plan`` request
pipeline. The re-exports below keep every previously-importable
``manager_bridge.*`` name (including ones exercised via ``monkeypatch``)
resolvable exactly as before.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import manager_dispatch, manager_pending_question, manager_state
from .manager_dispatch import (
    _build_handoff,
    _cancelled_result,
    _classify_operator_turn,
    _dispatch_team_mission,
    _handle_abort_control,
    _handle_authorization_control,
    _handle_pending_question_turn,
    _handle_steer_control,
    _item_to_dict,
    _maybe_apply_config_intent,
    _maybe_greeting_reply,
    _run_triage_and_fallbacks,
    _TurnEmitter,
)
from .manager_pending_question import _emit_ui_turn
from .manager_state import _chat_state_for, _lock_for

_PLAN_PREVIEW_CACHE_TTL_S = 60.0


def manager_message(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
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
    classify, greeting shortcut, authorization/steer/abort control, config
    intent, triage+fallbacks, TEAM dispatch) — each either returns a terminal
    result or ``None``/a small typed result to let the next phase run. This
    mirrors the original single-function control flow exactly; only the
    boilerplate (streaming/journaling a reply) is now shared via
    ``_TurnEmitter``.
    """
    from ..core.transcript import append_turn
    from ..life.memory import MemoryBundle
    from ..manager.front_door import mission_is_running

    body = (text or "").strip()
    if not body:
        return {"kind": "error", "reply": "empty message"}

    def _cancelled() -> bool:
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
            payload = {**payload, "message_id": f"{turn_id}-argus"}
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — UI progress must never break a turn
            pass

    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(global_root) if global_root else None
    )
    life_dir = mem.project_root

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
        turn_id = f"web-{time.time_ns()}"
        emitter = _TurnEmitter(life_dir=life_dir, turn_id=turn_id, fragment=_fragment)

        active_mission = mission_is_running(mem)

        # A web-process restart necessarily loses the live ACP process. Resume
        # seamlessly by opening one new warm conversation session with a
        # structured handoff built from the transcript that existed BEFORE this
        # operator turn. This is a restart seam only; ordinary turns remain on
        # the same live process + session.
        startup_handoff = ""
        if chat_state.pop("needs_startup_handoff", False):
            try:
                transcript = Path(life_dir) / "transcript.jsonl"
                if transcript.exists() and transcript.stat().st_size > 0:
                    startup_handoff = _build_handoff(life_dir)
                    chat_state["startup_handoffs"] = int(chat_state.get("startup_handoffs", 0)) + 1
            except Exception:  # noqa: BLE001 — continuity is best-effort
                pass

        # Journal the operator turn (transcript.jsonl role=operator) for
        # resume/replay. Best-effort — never block the reply.
        try:
            append_turn(life_dir, "operator", body)
        except Exception:  # noqa: BLE001
            pass
        _emit_ui_turn(life_dir, "operator", body, message_id=f"{turn_id}-operator")

        pending_questions = [
            item
            for item in mem.backlog.all()
            if str(getattr(item, "pending_question", "") or "").strip()
        ]
        pending_result = _handle_pending_question_turn(
            mem, pending_questions, body, chat_state, emitter
        )
        if pending_result is not None:
            return pending_result

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

        if control == "no_dispatch":
            route = "simple"

        if control == "abort":
            if _cancelled():
                return _cancelled_result()
            return _handle_abort_control(body, life_dir, emitter)

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
            return triage_result

        # 2) TEAM/complex — always enter the vertical-aware continuous lifecycle.
        # Chat and simple one-turn work already returned above.
        #
        # If the project lifecycle is ``done``, auto-resume it so the new
        # work can actually be picked up by the daemon.  Quarantined/archived
        # projects raise RuntimeError which is caught below and returned as a
        # structured ``{"kind": "error"}`` response — never a bare HTTP 500.
        if _cancelled():
            return _cancelled_result()
        try:
            item, daemon_alive, daemon_pid = _dispatch_team_mission(
                mem, body, chat_state, root_task_id, _cancelled, emitter
            )
        except Exception as exc:  # noqa: BLE001
            if _cancelled():
                return _cancelled_result()
            error_reply = f"could not enqueue: {exc}"
            return emitter.respond(error_reply, {"kind": "error"})

    item_payload = _item_to_dict(item, body)
    result = {
        "kind": "task",
        "reply": None,
        "item": item_payload,
        "daemon_alive": bool(daemon_alive),
        "daemon_pid": daemon_pid,
        "continuous": bool(chat_state.get("config", {}).get("continuous")),
    }
    title = str(
        (item_payload or {}).get("title")
        or (item_payload or {}).get("objective")
        or body
    )
    emitter.emit_only(f"Queued · {title}")
    return result

def manager_plan(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Draft one bounded execution plan through the configured Planner role."""
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import (
        resolve_knob,
        resolve_role_backend,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )
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
        planner_model = resolve_role_model(
            "planner",
            role_env="ARGUS_SKILL_PLAN_MODEL",
        )
        preview_model = resolve_knob(
            "ARGUS_SKILL_PLAN_PREVIEW_MODEL",
            "auto",
        ).value.strip()
        if preview_model.lower() in {"", "auto", "inherit", "default"}:
            planner_backend = normalize_runner_backend(
                resolve_role_backend("planner")
            )
            model = (
                "gpt-5.4-mini"
                if planner_backend in {"codex", "copilot"}
                else planner_model
            )
        else:
            model = preview_model
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
                lines.append(f"- working directory: {workdir}")
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
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import (
        resolve_knob,
        resolve_role_backend,
        resolve_role_model,
        resolve_role_reasoning_effort,
    )
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
        manager_model = resolve_role_model(
            "manager",
            role_env="ARGUS_SKILL_MANAGER_MODEL",
        )
        preview_model = resolve_knob(
            "ARGUS_SKILL_REWRITE_MODEL",
            "auto",
        ).value.strip()
        if preview_model.lower() in {"", "auto", "inherit", "default"}:
            manager_backend = normalize_runner_backend(resolve_role_backend("manager"))
            model = (
                "gpt-5.4-mini"
                if manager_backend in {"codex", "copilot"}
                else manager_model
            )
        else:
            model = preview_model
        effort = resolve_role_reasoning_effort(
            "ARGUS_SKILL_REWRITE_REASONING_EFFORT",
            default="low",
        )
        rewrite = rewrite_prompt(
            runner,
            body,
            model=model,
            reasoning_effort=effort,
            run_label="manager-rewrite",
            project_context=_rewrite_project_context(mem, sid),
        )
    return {
        "original": rewrite.original or body,
        "rewritten": rewrite.rewritten,
        "changes": list(rewrite.changes),
        "questions": list(rewrite.questions),
        "error": rewrite.error,
    }


# --- Re-exports -------------------------------------------------------------
# Everything below moved out of this module into manager_state.py /
# manager_pending_question.py / manager_dispatch.py as part of a
# behavior-preserving decomposition. These plain attribute assignments keep
# every previously-importable ``manager_bridge.X`` name (including names
# exercised via ``monkeypatch.setattr(manager_bridge, "X", ...)`` in tests)
# resolvable exactly as before.

# manager_state.py
_STATES = manager_state._STATES
_LOCKS = manager_state._LOCKS
_REGISTRY_LOCK = manager_state._REGISTRY_LOCK
_MANAGER_PREWARMING = manager_state._MANAGER_PREWARMING
_MANAGER_PREWARMING_LOCK = manager_state._MANAGER_PREWARMING_LOCK
manager_context_lock = manager_state.manager_context_lock
_release_manager_state = manager_state._release_manager_state
release_manager_context = manager_state.release_manager_context
_prewarm_manager_context = manager_state._prewarm_manager_context
schedule_manager_prewarm = manager_state.schedule_manager_prewarm
_rotate_after = manager_state._rotate_after
reset_manager_context = manager_state.reset_manager_context
shutdown_manager_bridge = manager_state.shutdown_manager_bridge

# manager_pending_question.py
_parse_pending_question_decision = (
    manager_pending_question._parse_pending_question_decision
)
_resolve_pending_question_with_manager = (
    manager_pending_question._resolve_pending_question_with_manager
)
manager_answer_pending_question = (
    manager_pending_question.manager_answer_pending_question
)
record_task_dispatch_ack = manager_pending_question.record_task_dispatch_ack

# manager_dispatch.py
_NO_DISPATCH_FALLBACK = manager_dispatch._NO_DISPATCH_FALLBACK
_authorization_workdir = manager_dispatch._authorization_workdir
_project_paths_overlap = manager_dispatch._project_paths_overlap
manager_execution_handoff = manager_dispatch.manager_execution_handoff
manager_continuous_handoff = manager_dispatch.manager_continuous_handoff
disable_manager_continuous = manager_dispatch.disable_manager_continuous
manager_bounded_handoff = manager_dispatch.manager_bounded_handoff
_journal_argus_reply = manager_dispatch._journal_argus_reply
_ClassifyResult = manager_dispatch._ClassifyResult
