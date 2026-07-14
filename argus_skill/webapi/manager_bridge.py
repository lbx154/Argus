"""Bridge the Web/Ink front-end to the Manager routing pipeline.

An operator message is NOT blindly turned into a backlog task. It goes through
``manager_triage`` — chat-vs-task classification + an inline reply for chat/SELF
work. A conversational "你好" gets a Manager reply and never touches the daemon
or a vertical; only TEAM/complex work is enqueued as a mission.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

# Per-project chat_state cache: keeps the Manager runner + codex/copilot thread
# id warm across turns so a conversation stays coherent and each message doesn't
# rebuild the runner. Keyed by sid. A per-sid lock serialises triage for one
# project (chat_state is mutated in place) while letting different projects run
# concurrently.
_STATES: dict[str, dict[str, Any]] = {}
_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCK = threading.Lock()
_NO_DISPATCH_FALLBACK = (
    "[not dispatched] The Manager kept this request inline as instructed, but "
    "could not complete the read-only reply. No task was queued and no daemon "
    "was started."
)
_PLAN_PREVIEW_CACHE_TTL_S = 60.0


def manager_execution_handoff(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    root_task_id: str | None = None,
) -> str:
    """Resolve a direct Web/TUI command into Manager's role-clean handoff."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_execution_task

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        return manager_execution_task(
            mem,
            text,
            chat_state,
            root_task_id=root_task_id,
        )


def manager_continuous_handoff(
    sid: str,
    requested_objective: str,
    *,
    global_root: Path | str | None = None,
    name_session: bool = False,
) -> str:
    """Atomically enable a Manager-authored continuous handoff."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_continuous_handoff as commit_handoff

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        if name_session:
            from ..manager.config_intent import _front_door_classify

            _front_door_classify(mem, requested_objective, chat_state)
        execution_objective = commit_handoff(mem, requested_objective, chat_state)
        chat_state.setdefault("config", {})["continuous"] = True
        chat_state["continuous_objective"] = execution_objective
        return execution_objective


def disable_manager_continuous(
    sid: str,
    *,
    life_dir: Path,
) -> None:
    """Persist Web stop and synchronize Manager state under one session lock."""
    from ..daemon.state import disable_continuous_config
    from ..manager.front_door import ManagerHandoffError

    with _lock_for(sid):
        persisted = disable_continuous_config(life_dir)
        if persisted.enabled:
            raise ManagerHandoffError("continuous stop could not be persisted")
        chat_state = _STATES.get(sid)
        if chat_state is None:
            return
        chat_state.setdefault("config", {})["continuous"] = False
        chat_state["continuous_objective"] = ""
        chat_state.pop("_continuous_pending_manager_handoff", None)


def manager_bounded_handoff(
    sid: str,
    text: str,
    persist: Any,
    *,
    global_root: Path | str | None = None,
    root_task_id: str | None = None,
    name_session: bool = False,
) -> Any:
    """Commit Manager state and caller persistence under one pipeline lock."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import manager_bounded_handoff as commit_handoff

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _lock_for(sid):
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        if name_session:
            from ..core.session import read_session_meta

            meta = read_session_meta(mem.global_root, sid)
            if meta is None or not meta.display_name.strip():
                from ..manager.config_intent import _front_door_classify

                _front_door_classify(
                    mem,
                    text,
                    chat_state,
                    root_task_id=root_task_id,
                )
        return commit_handoff(
            mem,
            text,
            chat_state,
            persist,
            root_task_id=root_task_id,
        )


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


def _lock_for(sid: str) -> threading.RLock:
    with _REGISTRY_LOCK:
        lk = _LOCKS.get(sid)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[sid] = lk
        return lk


def _chat_state_for(sid: str) -> dict[str, Any]:
    st = _STATES.get(sid)
    if st is not None:
        return st
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import resolve_role_backend
    from ..manager.dispatch import DEFAULT_MANAGER_CONFIG

    try:
        backend = normalize_runner_backend(resolve_role_backend("manager"))
    except Exception:  # noqa: BLE001
        backend = "codex"
    st = {
        "backend": backend,
        "last_thread_id": None,
        # The first message handled by this web process may belong to an older
        # persisted conversation. Seed the newly-warm ACP chat session from its
        # transcript once; a brand-new project has no prior transcript and skips
        # the handoff.
        "needs_startup_handoff": True,
        "session_started_s": time.monotonic(),
        "mission_count": 0,
        "config": dict(DEFAULT_MANAGER_CONFIG),
        "continuous_objective": "",
    }
    _STATES[sid] = st
    return st


def _item_to_dict(item: Any, fallback_title: str) -> dict[str, Any] | None:
    if item is None:
        return None
    for attr in ("to_dict", "asdict", "_asdict"):
        fn = getattr(item, attr, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:  # noqa: BLE001
                break
    try:
        return dict(item)  # mapping-like
    except Exception:  # noqa: BLE001
        return {
            "id": getattr(item, "id", None),
            "title": getattr(item, "title", fallback_title),
            "status": getattr(item, "status", "pending"),
        }


def _rotate_after() -> int:
    """Turns before the Manager session is rotated (a proxy for its context
    filling). Override with ARGUS_SKILL_MANAGER_ROTATE_TURNS."""
    import os

    try:
        return max(4, int(os.environ.get("ARGUS_SKILL_MANAGER_ROTATE_TURNS", "40")))
    except ValueError:
        return 40


def _build_handoff(life_dir: Any) -> str:
    """A STRUCTURED handoff seeded as the first message of a fresh Manager
    session when the old one's context fills. Minimal by design (the operator's
    rule: don't pre-chew — give the identity + where the logs live, and let the
    Manager read them itself): who it is, the project path, and the last few
    turns for continuity. Everything else it self-serves.
    """
    lines = [
        "[SESSION HANDOFF — the previous Manager session filled its context and was rotated.",
        "You are the Argus Manager for this project — the SINGLE interface between the operator",
        "and the autonomous research system (a black box to them). You reply to chat, dispatch",
        "real work to the planner/engineer/reviewer team, and answer 'what's happening' by",
        f"reading the project's own logs. Project workspace / logs: {life_dir}",
        "You can read events.jsonl / backlog.jsonl / transcript.jsonl there yourself — check state",
        "from those, do not expect it spoon-fed.",
    ]
    try:
        from ..core.transcript import read_turns

        turns = read_turns(life_dir, limit=6)
        if turns:
            lines.append("Recent conversation:")
            for t in turns:
                who = "operator" if str(t.get("role")) == "operator" else "you(Argus)"
                lines.append(f"  {who}: {str(t.get('text', '')).strip()[:200]}")
    except Exception:  # noqa: BLE001
        pass
    lines.append("Continue seamlessly.]")
    return "\n".join(lines)


def manager_message(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    on_fragment: Any = None,
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
    """
    from ..core.transcript import append_turn
    from ..life.memory import MemoryBundle
    from ..manager.config_intent import _apply_config_intent, _front_door_classify
    from ..manager.dispatch import enqueue_mission, maybe_promote_to_continuous
    from ..manager.front_door import _accepts_keyword, manager_triage

    body = (text or "").strip()
    if not body:
        return {"kind": "error", "reply": "empty message"}

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
        if kind == "delta":
            payload = {**payload, "message_id": f"{turn_id}-argus"}
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — UI progress must never break a turn
            pass

    def _phase(label: str) -> None:
        _fragment("phase", {"role": "manager", "label": label})

    mem = MemoryBundle.for_cwd(
        fingerprint=sid, global_root=Path(global_root) if global_root else None
    )
    life_dir = mem.project_root

    lock = _lock_for(sid)
    with lock:
        chat_state = _chat_state_for(sid)
        chat_state["session_id"] = sid
        chat_state["global_root"] = str(mem.global_root)
        turn_id = f"web-{time.time_ns()}"

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

        # Emit the stage BEFORE the classifier call. Copilot ACP may produce no
        # protocol events while the model is reasoning, so without this real
        # transition the TUI can only show its generic rotating slogan.
        _phase("Manager · classifying this message")

        # Persistent Manager session with context-rotation: it stays alive (the
        # codex/copilot thread is resumed via last_thread_id each turn) and is
        # only ROTATED when its context fills — a fresh thread seeded with a
        # STRUCTURED handoff (identity + project path + recent turns), so the
        # operator never notices the seam. Turn count is a cheap proxy for "full".
        chat_state["turns"] = int(chat_state.get("turns", 0)) + 1
        send_body = f"{startup_handoff}\n\n{body}" if startup_handoff else body
        from ..life import BacklogItem

        root_task_id = BacklogItem.new_id()
        if chat_state["turns"] > _rotate_after():
            send_body = f"{_build_handoff(life_dir)}\n\n{body}"
            chat_state["last_thread_id"] = None  # start a fresh session thread
            # The cached runner keeps its OWN copy of the session id
            # (``_next_seed_thread_id``); ``_simple_quick_reply`` falls back to it
            # when ``seed_thread_id`` is None, so clearing only ``last_thread_id``
            # here let the runner RESURRECT the just-rotated thread — rotation never
            # took and the codex/copilot session grew unbounded (its resume cost
            # climbing every turn). Reset the runner's memory too so the fresh
            # thread is genuinely fresh.
            _runner = chat_state.get("manager_runner")
            if _runner is not None and hasattr(_runner, "reset_chat_session"):
                try:
                    _runner.reset_chat_session()
                except Exception:  # noqa: BLE001 — rotation must never break the turn
                    pass
            chat_state["turns"] = 1
            chat_state["rotations"] = int(chat_state.get("rotations", 0)) + 1

        # ONE merged front-door call decides config, control, route, TEAM
        # lifetime, title, and an optional lightweight SELF reply. A natural-language
        # config change ("set the engineer to xhigh", "use copilot for reviewer",
        # "cap the budget at $10") is applied + confirmed inline and NEVER
        # enqueued; otherwise the reusable decisions avoid a second route/lifetime
        # call, and pure chat/capability turns may finish from this same response.
        # Classification is stateless and must see ONLY the current operator
        # message. Feeding it the startup/context-rotation handoff can make a
        # greeting look like a complex systems task; the enriched body belongs
        # only in the conversational reply session below.
        from ..manager.front_door import mission_is_running

        active_mission = mission_is_running(mem)
        classify_kwargs = (
            {"root_task_id": root_task_id}
            if _accepts_keyword(_front_door_classify, "root_task_id")
            else {}
        )
        decision = _front_door_classify(
            mem,
            body,
            chat_state,
            **classify_kwargs,
        )
        if isinstance(decision, tuple) and len(decision) == 3:
            intent, control, route = decision
        else:
            intent, route = decision
            control = None

        fast_reply = str(
            chat_state.pop("_frontdoor_fast_reply", "") or ""
        ).strip()
        if (
            fast_reply
            and intent is None
            and control is None
            and route == "simple"
            and send_body == body
        ):
            _fragment("delta", {"text": fast_reply})
            try:
                append_turn(life_dir, "argus", fast_reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                fast_reply,
                message_id=f"{turn_id}-argus",
            )
            return {"kind": "chat", "reply": fast_reply}

        if control == "steer":
            from ..apps._inbox import queue_inbox_message

            directive = (
                "[MANAGER STEERING — highest priority for the current mission] "
                + body
            )
            queue_inbox_message(
                life_dir,
                directive,
                source="manager.steer",
            )
            reply = (
                "已向当前 Engineer/Planner 写入最高优先级 steering 指令；"
                "下一轮必须先处理这条方向调整。"
            )
            _fragment("delta", {"text": reply})
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                reply,
                message_id=f"{turn_id}-argus",
            )
            return {
                "kind": "control",
                "control": "steer",
                "reply": reply,
            }

        if (active_mission or mission_is_running(mem)) and control != "abort":
            _phase("Manager · responding while the current mission continues")
            route = "simple"

        if control == "no_dispatch":
            route = "simple"

        if control == "abort":
            from ..tools.mission_control import request_current_mission_abort

            requested, item_id = request_current_mission_abort(
                life_dir,
                reason=f"operator requested: {body}",
                requested_by="manager",
            )
            reply = (
                f"Stop requested for running task {item_id}."
                if requested
                else "No running task to abort. Pending tasks were left unchanged."
            )
            _fragment("delta", {"text": reply})
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                reply,
                message_id=f"{turn_id}-argus",
            )
            return {
                "kind": "control",
                "control": "abort",
                "reply": reply,
                "requested": requested,
                "item_id": item_id,
            }

        cfg_lines: list[str] = []
        if intent is not None:
            try:
                applied = _apply_config_intent(mem, intent, chat_state, on_confirm=cfg_lines.append)
            except Exception:  # noqa: BLE001 — a config-apply hiccup must never block the message
                applied = False
            if applied:
                if on_fragment is not None:
                    for _ln in cfg_lines:
                        _fragment("delta", {"text": _ln, "message_id": "config"})
                reply = "\n".join(cfg_lines).strip() or "Done — setting applied."
                try:
                    append_turn(life_dir, "argus", reply)
                except Exception:  # noqa: BLE001
                    pass
                _emit_ui_turn(life_dir, "argus", reply, message_id=f"{turn_id}-argus")
                return {"kind": "chat", "reply": reply}

        # 1) Manager triage — chat/SELF returns a reply; TEAM returns None. The
        # route was already decided in the merged call above, so triage skips its
        # own route classify (``route=route``).
        try:
            reply = manager_triage(
                mem,
                send_body,
                chat_state,
                on_fragment=_fragment if callable(on_fragment) else None,
                route=route,
                root_task_id=root_task_id,
            )
        except Exception:  # noqa: BLE001 — triage failure biases to task
            reply = None

        if reply is not None:
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(life_dir, "argus", reply, message_id=f"{turn_id}-argus")
            return {"kind": "chat", "reply": reply}
        if control == "no_dispatch":
            reply = _NO_DISPATCH_FALLBACK
            _fragment("delta", {"text": reply})
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                reply,
                message_id=f"{turn_id}-argus",
            )
            return {"kind": "chat", "reply": reply}
        if mission_is_running(mem):
            reply = (
                "[not dispatched] A mission became active while this message "
                "was being handled; it was not added to the backlog."
            )
            _fragment("delta", {"text": reply})
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                reply,
                message_id=f"{turn_id}-argus",
            )
            return {"kind": "chat", "reply": reply}

        # 2) TEAM/complex — let Manager own lifetime before enqueue. Chat and
        # simple one-turn work already returned above, so ambiguity defaults to
        # STANDING; only an explicit Manager BOUNDED verdict remains one-shot.
        try:
            if not chat_state.get("config", {}).get("continuous", False):
                _phase("Manager · deciding task lifetime")
                maybe_promote_to_continuous(
                    mem,
                    body,
                    chat_state,
                    root_task_id=root_task_id,
                )
            item, daemon_alive, daemon_pid = enqueue_mission(
                mem,
                body,
                chat_state,
                root_task_id=root_task_id,
            )
        except Exception as exc:  # noqa: BLE001
            error_reply = f"could not enqueue: {exc}"
            _emit_ui_turn(life_dir, "argus", error_reply, message_id=f"{turn_id}-argus")
            return {"kind": "error", "reply": error_reply}

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
    _emit_ui_turn(life_dir, "argus", f"Queued · {title}", message_id=f"{turn_id}-argus")
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


def reset_manager_context(
    sid: str, *, global_root: Path | str | None = None,
) -> bool:
    """Drop the warm Manager conversation while preserving project state."""
    from ..manager import reset_manager_session

    root = Path(global_root) if global_root else None
    life_dir = (root / "projects" / sid) if root is not None else None
    if life_dir is None:
        from ..core import paths as core_paths
        life_dir = core_paths.global_root() / "projects" / sid
    if not life_dir.is_dir():
        return False
    with _lock_for(sid):
        state = _STATES.get(sid)
        runner = state.get("manager_runner") if state else None
        if runner is not None and hasattr(runner, "reset_chat_session"):
            try:
                runner.reset_chat_session()
            except Exception:  # noqa: BLE001
                pass
        _STATES.pop(sid, None)
        reset_manager_session(life_dir)
    return True


def shutdown_manager_bridge() -> None:
    """Release warm Manager runners and Copilot ACP children on Web shutdown."""
    with _REGISTRY_LOCK:
        states = list(_STATES.values())
        _STATES.clear()
        _LOCKS.clear()
    for state in states:
        runner = state.get("manager_runner")
        if runner is not None and hasattr(runner, "reset_chat_session"):
            try:
                runner.reset_chat_session()
            except Exception:  # noqa: BLE001
                pass
    try:
        from ..agent_cli.copilot_acp import close_all_clients

        close_all_clients()
    except Exception:  # noqa: BLE001
        pass
