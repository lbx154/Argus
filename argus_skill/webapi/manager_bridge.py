"""Bridge the web/TUI front-door to the SAME Manager triage the Python REPL uses.

An operator message is NOT blindly turned into a backlog task. It goes through
``manager_triage`` — chat-vs-task classification + an inline reply for chat/SELF
work — exactly like the line REPL (``manager/repl.py``): a conversational
"你好" gets a Manager reply and never touches the daemon or a vertical; only
TEAM/complex work is enqueued as a mission (where the daemon resolves a vertical).

This reuses the REPL's ``manager_triage`` + ``enqueue_mission`` verbatim — no
reimplementation, no second front-door, no drift from the terminal behaviour.
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
_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


def _lock_for(sid: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        lk = _LOCKS.get(sid)
        if lk is None:
            lk = threading.Lock()
            _LOCKS[sid] = lk
        return lk


def _chat_state_for(sid: str) -> dict[str, Any]:
    st = _STATES.get(sid)
    if st is not None:
        return st
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import resolve_role_backend
    from ..manager.repl import _CONFIG_DEFAULTS

    try:
        backend = normalize_runner_backend(resolve_role_backend("manager"))
    except Exception:  # noqa: BLE001
        backend = "codex"
    st = {
        "backend": backend,
        "theme": None,
        "last_thread_id": None,
        # The first message handled by this web process may belong to an older
        # persisted conversation. Seed the newly-warm ACP chat session from its
        # transcript once; a brand-new project has no prior transcript and skips
        # the handoff.
        "needs_startup_handoff": True,
        "session_started_s": time.monotonic(),
        "mission_count": 0,
        "config": dict(_CONFIG_DEFAULTS),
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
    from ..manager.repl import (
        _apply_config_intent,
        _front_door_classify,
        enqueue_mission,
        manager_triage,
    )

    body = (text or "").strip()
    if not body:
        return {"kind": "error", "reply": "empty message"}

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
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
        # resume/replay, mirroring the REPL. Best-effort — never block the reply.
        try:
            append_turn(life_dir, "operator", body)
        except Exception:  # noqa: BLE001
            pass

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

        # 0+1) ONE merged front-door classify: config-intent + route in a SINGLE
        # LLM call (was two sequential copilot cold-starts). A natural-language
        # config change ("set the engineer to xhigh", "use copilot for reviewer",
        # "cap the budget at $10") is applied + confirmed inline and NEVER
        # enqueued; otherwise the precomputed route is handed to triage so it does
        # not re-classify.
        # Classification is stateless and must see ONLY the current operator
        # message. Feeding it the startup/context-rotation handoff can make a
        # greeting look like a complex systems task; the enriched body belongs
        # only in the conversational reply session below.
        intent, route = _front_door_classify(mem, body, chat_state)

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
                return {"kind": "chat", "reply": reply}

        # 1) Manager triage — chat/SELF returns a reply; TEAM returns None. The
        # route was already decided in the merged call above, so triage skips its
        # own route classify (``route=route``).
        try:
            reply = manager_triage(mem, send_body, chat_state, on_fragment=on_fragment, route=route)
        except Exception:  # noqa: BLE001 — triage failure → task path (same as REPL)
            reply = None

        if reply is not None:
            try:
                append_turn(life_dir, "argus", reply)
            except Exception:  # noqa: BLE001
                pass
            return {"kind": "chat", "reply": reply}

        # 2) TEAM/complex — enqueue a mission (daemon resolves the vertical there).
        try:
            item, daemon_alive, daemon_pid = enqueue_mission(mem, body, chat_state)
        except Exception as exc:  # noqa: BLE001
            return {"kind": "error", "reply": f"could not enqueue: {exc}"}

    return {
        "kind": "task",
        "reply": None,
        "item": _item_to_dict(item, body),
        "daemon_alive": bool(daemon_alive),
        "daemon_pid": daemon_pid,
    }
