"""Bridge the Web/Ink front-end to the Manager routing pipeline.

An operator message is NOT blindly turned into a backlog task. It goes through
``manager_triage`` — chat-vs-task classification + an inline reply for chat/SELF
work. A conversational "你好" gets a Manager reply and never touches the daemon
or a vertical; only TEAM/complex work is enqueued as a mission.
"""

from __future__ import annotations

import json
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Per-project chat_state cache: keeps the Manager runner + codex/copilot thread
# id warm across turns so a conversation stays coherent and each message doesn't
# rebuild the runner. Keyed by sid. A per-sid lock serialises triage for one
# project (chat_state is mutated in place) while letting different projects run
# concurrently.
_STATES: dict[str, dict[str, Any]] = {}
_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_REGISTRY_LOCK = threading.Lock()
_MANAGER_PREWARMING: set[str] = set()
_MANAGER_PREWARMING_LOCK = threading.Lock()
_NO_DISPATCH_FALLBACK = (
    "[not dispatched] The Manager kept this request inline as instructed, but "
    "could not complete the read-only reply. No task was queued and no daemon "
    "was started."
)
_PLAN_PREVIEW_CACHE_TTL_S = 60.0


def _authorization_workdir(
    chat_state: dict[str, Any],
    life_dir: Path,
) -> Path:
    from ..manager.front_door import _operator_workspace

    return _operator_workspace(chat_state, life_dir)


def _project_paths_overlap(left: object, right: object) -> bool:
    left_path = Path(str(left or "").strip().replace("\\", "/"))
    right_path = Path(str(right or "").strip().replace("\\", "/"))
    return bool(
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )

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


def _parse_pending_question_decision(text: str) -> dict[str, Any] | None:
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

    question = str(getattr(item, "pending_question", "") or "").strip()
    prompt = (
        "You are the Manager resolving an operator-only blocker for an existing "
        "mission. Interpret the operator response in the blocked mission context. "
        "Return ONLY one JSON object with exactly these fields: "
        '{"is_answer": boolean, "resolved": boolean, "decision": string, '
        '"reply": string}. Set is_answer=false when the message is unrelated '
        "chat, status, configuration, or control rather than an attempted answer; "
        "in that case also set resolved=false and leave decision and reply empty. "
        "Set resolved=true only when the response supplies enough authority or "
        "information for the team to continue. decision must then be an explicit, "
        "role-clean instruction for Planner/Engineer. If it is unrelated or "
        "insufficient, set resolved=false, keep decision empty, and use reply to "
        "ask one concise clarification question.\n\n"
        f"Blocked item id: {item.id}\n"
        f"Blocked mission title: {item.title}\n"
        f"Blocked mission objective:\n{item.objective}\n\n"
        f"Reviewer question:\n{question}\n\n"
        f"Operator response:\n{answer.strip()}"
    )
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
    with _lock_for(sid):
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
        from ..core import paths as core_paths
        root = core_paths.global_root()
    life_dir = root / "projects" / sid

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
            on_fragment("delta", {"text": text, "message_id": "dispatch"})
        except Exception:  # noqa: BLE001 — UI progress must never break dispatch
            pass

    result["reply"] = text
    return text


def _lock_for(sid: str) -> threading.RLock:
    with _REGISTRY_LOCK:
        lk = _LOCKS.get(sid)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[sid] = lk
        return lk


@contextmanager
def manager_context_lock(sid: str) -> Iterator[None]:
    """Serialize a project lifecycle change with Manager turns."""
    with _lock_for(sid):
        yield


def _release_manager_state(sid: str) -> None:
    state = _STATES.pop(sid, None)
    runner = state.get("manager_runner") if state else None
    if runner is not None:
        try:
            backend = getattr(runner, "_backend", None)
            close_acp = getattr(backend, "close_acp_clients", None)
            if callable(close_acp):
                close_acp()
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(runner, "reset_chat_session"):
                runner.reset_chat_session()
        except Exception:  # noqa: BLE001
            pass


def release_manager_context(sid: str) -> None:
    """Release one warm Manager runner without touching project files."""
    with _lock_for(sid):
        _release_manager_state(sid)


def _prewarm_manager_context(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> None:
    from ..life.memory import MemoryBundle
    from ..manager.front_door import _ensure_manager_runner

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _lock_for(sid):
        if not mem.project_root.is_dir():
            return
        state = _chat_state_for(sid)
        if state.get("_manager_acp_prewarmed") or state.get("backend") != "copilot":
            return
        state["session_id"] = sid
        state["global_root"] = str(mem.global_root)
        runner = _ensure_manager_runner(state, mem)
        backend = getattr(runner, "_backend", None) if runner is not None else None
        prewarm = getattr(backend, "prewarm_acp_client", None)
        if not callable(prewarm):
            return
        import os

        from ..core.knobs import (
            resolve_manager_classify_model,
            resolve_manager_reply_model,
            resolve_role_reasoning_effort,
        )

        cwd = str(state.get("manager_runner_workdir") or Path.cwd())
        classify_effort = (
            os.environ.get("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "low").strip()
            or "low"
        )
        prewarm(
            model=resolve_manager_classify_model(),
            reasoning_effort=classify_effort,
            lean=True,
            cwd=cwd,
            front_door_session=True,
        )
        prewarm(
            model=resolve_manager_reply_model(),
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_SELF_REASONING_EFFORT",
                default="xhigh",
            ),
            lean=False,
            cwd=cwd,
        )
        state["_manager_acp_prewarmed"] = True


def schedule_manager_prewarm(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> None:
    """Warm exactly one project's private Manager ACP pool in background."""
    with _MANAGER_PREWARMING_LOCK:
        if sid in _MANAGER_PREWARMING:
            return
        _MANAGER_PREWARMING.add(sid)

    def _run() -> None:
        try:
            _prewarm_manager_context(sid, global_root=global_root)
        except Exception:  # noqa: BLE001 - project selection must stay available
            pass
        finally:
            with _MANAGER_PREWARMING_LOCK:
                _MANAGER_PREWARMING.discard(sid)

    threading.Thread(
        target=_run,
        name=f"manager-prewarm-{sid}",
        daemon=True,
    ).start()


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
    """
    from ..core.transcript import append_turn
    from ..life.memory import BacklogItem, MemoryBundle
    from ..manager.config_intent import _apply_config_intent, _front_door_classify
    from ..manager.dispatch import (
        enqueue_mission,
        maybe_promote_to_continuous,
        resume_done_lifecycle_for_team_dispatch,
    )
    from ..manager.front_door import (
        _accepts_keyword,
        manager_triage,
    )

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

    def _cancelled_result() -> dict[str, Any]:
        return {
            "kind": "cancelled",
            "reply": "Manager request cancelled; no task was dispatched.",
        }

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

        from ..manager.front_door import mission_is_running

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
        if len(pending_questions) == 1:
            _phase("Manager · interpreting your answer to the blocked mission")
            result = _resolve_pending_question_with_manager(
                mem,
                pending_questions[0],
                body,
                chat_state,
                root_task_id=BacklogItem.new_id(),
            )
            if result.get("answer_intent") is not False:
                reply = str(
                    result.get("reply")
                    or result.get("error")
                    or "Manager could not resolve the pending question."
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
                return {"kind": "pending_question", "reply": reply, **result}
        if len(pending_questions) > 1:
            reply = (
                "More than one task needs your input. Open the Needs you prompt "
                "for the specific task you want to answer."
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
            return {"kind": "pending_question_choice", "reply": reply}

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
        # lifetime, title, vertical, and a strict pure-greeting token. A natural-language
        # config change ("set the engineer to xhigh", "use copilot for reviewer",
        # "cap the budget at $10") is applied + confirmed inline and NEVER
        # enqueued; otherwise the reusable decisions avoid a second route/lifetime
        # call. Classifier output is never an operator-facing reply; every SELF
        # message reaches the actual Manager model.
        # Classification is stateless and must see ONLY the current operator
        # message. Feeding it the startup/context-rotation handoff can make a
        # greeting look like a complex systems task; the enriched body belongs
        # only in the conversational reply session below.
        classify_kwargs = (
            {"root_task_id": root_task_id}
            if _accepts_keyword(_front_door_classify, "root_task_id")
            else {}
        )
        if _accepts_keyword(_front_door_classify, "active_mission"):
            classify_kwargs["active_mission"] = active_mission
        decision = _front_door_classify(
            mem,
            body,
            chat_state,
            **classify_kwargs,
        )
        if _cancelled():
            return _cancelled_result()
        if isinstance(decision, tuple) and len(decision) == 3:
            intent, control, route = decision
        else:
            intent, route = decision
            control = None

        greeting_reply = str(
            chat_state.pop("_frontdoor_greeting_reply", "") or ""
        ).strip()
        frontdoor_failure = str(
            chat_state.pop("_frontdoor_failure", "") or ""
        ).strip()
        if (
            greeting_reply
            and intent is None
            and control is None
            and route == "simple"
            and send_body == body
        ):
            _fragment("delta", {"text": greeting_reply})
            try:
                append_turn(life_dir, "argus", greeting_reply)
            except Exception:  # noqa: BLE001
                pass
            _emit_ui_turn(
                life_dir,
                "argus",
                greeting_reply,
                message_id=f"{turn_id}-argus",
            )
            return {"kind": "chat", "reply": greeting_reply}

        authorization_actions = chat_state.pop(
            "_frontdoor_authorization",
            None,
        )
        if isinstance(authorization_actions, list) and authorization_actions:
            if _cancelled():
                return _cancelled_result()
            from ..manager.control_state import CampaignControlStore

            try:
                control_store = CampaignControlStore(
                    life_dir,
                    project_root=_authorization_workdir(chat_state, life_dir),
                )
                head = control_store.read_head()
                snapshot = control_store.read_snapshot(head)
                active_wait = snapshot.get("active_wait") if snapshot else None
                if head is None or not isinstance(active_wait, dict):
                    raise ValueError(
                        "no current Manager-bound blocker is awaiting authorization"
                    )
                terminal_evidence = list(
                    snapshot.get("terminal_evidence") or []
                ) if snapshot else []
                diagnosis = (
                    terminal_evidence[-1]
                    if terminal_evidence
                    and isinstance(terminal_evidence[-1], dict)
                    else {}
                )
                validator_repair = "validator_repair" in authorization_actions
                if (
                    validator_repair
                    and diagnosis.get("failure_source") != "validator_defect"
                ):
                    raise ValueError(
                        "current Reviewer diagnosis is not validator_defect"
                    )
                repair_paths = list(diagnosis.get("repair_paths") or [])
                validator_id = str(diagnosis.get("validator_id") or "")
                watched_paths = [
                    str(value)
                    for value in (active_wait.get("watched_paths") or [])
                    if not any(
                        _project_paths_overlap(value, repair_path)
                        for repair_path in repair_paths
                    )
                ]
                identity = control_store.campaign_identity(
                    campaign_epoch=head.campaign_epoch,
                )
                if (
                    identity.campaign_id != head.campaign_id
                    or identity.objective_sha256 != head.objective_sha256
                ):
                    raise ValueError("active campaign identity changed")
                authorization = control_store.issue_authorization(
                    identity=identity,
                    blocker_fingerprint=str(
                        active_wait.get("blocker_fingerprint") or ""
                    ),
                    allowed_actions=authorization_actions,
                    scope="active_blocker",
                    allowed_write_paths=repair_paths,
                    evidence_paths=watched_paths,
                    forbidden_mutations=watched_paths,
                    source_channel=source_channel,
                    source_message_id=source_message_id or turn_id,
                    validator_id=validator_id,
                    acceptance_retries=(
                        1 if validator_repair else 0
                    ),
                    expected_state_revision=head.state_revision,
                    expected_wait_id=str(active_wait.get("wait_id") or ""),
                )
                reply = (
                    "Authorization recorded for the current campaign blocker "
                    f"as {authorization.authorization_id}. No task was dispatched."
                )
                result = {
                    "kind": "control",
                    "control": "authorization",
                    "reply": reply,
                    "authorization_id": authorization.authorization_id,
                    "campaign_id": authorization.campaign_id,
                    "state_revision": authorization.state_revision,
                    "allowed_actions": list(authorization.allowed_actions),
                }
            except (OSError, TypeError, ValueError) as exc:
                reply = f"Authorization not recorded: {exc}. No task was dispatched."
                result = {
                    "kind": "control",
                    "control": "authorization_rejected",
                    "reply": reply,
                }
            _fragment("delta", {"text": reply, "message_id": "authorization"})
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
            return result

        if control == "steer":
            if _cancelled():
                return _cancelled_result()
            from ..apps._inbox import queue_inbox_message

            manager_directive = str(
                chat_state.pop("_frontdoor_steering_directive", "") or ""
            ).strip()
            if not manager_directive:
                reply = (
                    "我判断这属于当前任务的方向调整，但没有形成足够明确的团队指令；"
                    "本次未修改任务，请重试或补充目标。"
                )
                _fragment("delta", {"text": reply, "message_id": "steer"})
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
                    "kind": "chat",
                    "control": "steer_unresolved",
                    "reply": reply,
                }
            directive = (
                "[MANAGER STEERING — highest priority for the current mission] "
                + manager_directive
            )
            queue_inbox_message(
                life_dir,
                directive,
                source="manager.steer",
            )
            reply = f"我已调整团队方向：{manager_directive}"
            _fragment("delta", {"text": reply, "message_id": "steer"})
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
            if _cancelled():
                return _cancelled_result()
            from ..tools.mission_control import request_current_mission_abort

            requested, item_id = request_current_mission_abort(
                life_dir,
                reason=f"operator requested: {body}",
                requested_by="manager",
            )
            if requested:
                reply = f"Stop requested for running task {item_id}."
            elif item_id is not None:
                reply = f"Stop request failed for running task {item_id}."
            else:
                reply = (
                    "No running task to abort. Pending tasks were left unchanged."
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
            if _cancelled():
                return _cancelled_result()
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
        if route == "simple" and control != "no_dispatch":
            # The classifier already said SELF/chat. A failed inline Manager turn
            # must never fall through into TEAM dispatch — that queues greetings,
            # status questions, or capability chat as real missions precisely when
            # the Manager backend is unhealthy.
            reply = (
                "[not dispatched] Manager could not complete this inline reply. "
                "No task was queued; please retry the message."
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
        if frontdoor_failure:
            reply = (
                "[not dispatched] Manager could not classify this message. "
                "No task was queued; please retry."
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
        #
        # If the project lifecycle is ``done``, auto-resume it so the new
        # work can actually be picked up by the daemon.  Quarantined/archived
        # projects raise RuntimeError which is caught below and returned as a
        # structured ``{"kind": "error"}`` response — never a bare HTTP 500.
        if _cancelled():
            return _cancelled_result()
        try:
            resume_done_lifecycle_for_team_dispatch(mem)
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
                cancelled=_cancelled,
            )
        except Exception as exc:  # noqa: BLE001
            if _cancelled():
                return _cancelled_result()
            error_reply = f"could not enqueue: {exc}"
            _fragment("delta", {"text": error_reply})
            try:
                append_turn(life_dir, "argus", error_reply)
            except Exception:  # noqa: BLE001
                pass
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
        _release_manager_state(sid)
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
