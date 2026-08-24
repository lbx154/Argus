"""Manager chat-state, locks, and prewarm lifecycle for the webapi bridge.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Owns the per-project chat_state cache, the per-sid lock
registry, and the manager-runner prewarm bookkeeping so
``manager_bridge.py`` can stay focused on the ``manager_message`` /
``manager_plan`` request pipeline. Public names are re-exported from
``manager_bridge`` unchanged so existing imports/monkeypatches keep working.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core import paths as core_paths

log = logging.getLogger(__name__)

# Per-project chat_state cache: keeps the Manager runner + codex/copilot thread
# id warm across turns so a conversation stays coherent and each message doesn't
# rebuild the runner. Keyed by sid. A per-sid lock serialises triage for one
# project (chat_state is mutated in place) while letting different projects run
# concurrently.
_STATES: dict[str, dict[str, Any]] = {}
_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()
_REGISTRY_LOCK = threading.Lock()
_MANAGER_PREWARMING: set[str] = set()
_MANAGER_PREWARMING_LOCK = threading.Lock()
_MANAGER_PREWARM_OWNER: str | None = None
# Emergency natural-language pause bypasses the per-session Manager lock. A
# generation bump lets any older turn notice that it was superseded before it
# can commit/dispatch work after the operator has clocked the session out.
_CONTROL_GENERATIONS: dict[str, int] = {}
_DEFAULT_WARM_CONTEXT_LIMIT = 8
_DEFAULT_WARM_CONTEXT_IDLE_SECONDS = 30 * 60


def _lock_for(sid: str) -> threading.RLock:
    with _REGISTRY_LOCK:
        lk = _LOCKS.get(sid)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[sid] = lk
        return lk


def manager_control_generation(sid: str) -> int:
    """Return the current in-process operator-control generation."""
    with _REGISTRY_LOCK:
        return _CONTROL_GENERATIONS.get(sid, 0)


def interrupt_manager_turns(sid: str) -> int:
    """Supersede older Manager turns without waiting for their session lock.

    Persistent daemon/continuous state is changed by the pause handler. This
    in-process fence only prevents an already-running front-door turn from
    dispatching stale work after that durable pause lands.
    """
    with _REGISTRY_LOCK:
        generation = _CONTROL_GENERATIONS.get(sid, 0) + 1
        _CONTROL_GENERATIONS[sid] = generation
        state = _STATES.get(sid)
        if state is not None:
            state.setdefault("config", {})["continuous"] = False
            state["continuous_objective"] = ""
            state.pop("_continuous_pending_manager_handoff", None)
        return generation


@contextmanager
def manager_context_lock(sid: str) -> Iterator[None]:
    """Serialize a project lifecycle change with Manager turns."""
    with _lock_for(sid):
        yield


def _release_manager_state(sid: str) -> None:
    state = _STATES.pop(sid, None)
    with _REGISTRY_LOCK:
        _CONTROL_GENERATIONS.pop(sid, None)
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
    """Warm one lightweight classifier transport for the active project."""
    from ..life.memory import MemoryBundle
    from ..manager.front_door import _ensure_manager_runner

    mem = MemoryBundle.for_cwd(
        fingerprint=sid,
        global_root=Path(global_root) if global_root else None,
    )
    with _lock_for(sid):
        if not mem.project_root.is_dir():
            return
        if not _is_manager_prewarm_owner(sid):
            return
        state = _chat_state_for(sid, manager_activity=False)
        if state.get("_manager_acp_prewarmed") or state.get("backend") != "copilot":
            return
        state["session_id"] = sid
        state["global_root"] = str(mem.global_root)
        runner = _ensure_manager_runner(state, mem)
        default_backend = getattr(runner, "_backend", None) if runner is not None else None
        classifier_backend = None
        if runner is not None:
            classifier_backend = (
                getattr(runner, "manager_backend", None) or default_backend
            )
        prewarm_classifier = getattr(
            classifier_backend,
            "prewarm_acp_client",
            None,
        )
        prewarm_reply = getattr(default_backend, "prewarm_acp_client", None)
        if not callable(prewarm_classifier) or not callable(prewarm_reply):
            return
        from ..core.knobs import (
            resolve_knob,
            resolve_manager_classify_model,
            resolve_manager_reply_model,
            resolve_role_reasoning_effort,
        )

        cwd = str(state.get("manager_runner_workdir") or Path.cwd())
        classifier_backend_name = getattr(
            classifier_backend,
            "backend",
            state.get("backend"),
        )
        reply_backend_name = getattr(
            default_backend,
            "backend",
            state.get("backend"),
        )
        classify_effort = resolve_knob(
            "ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT",
            "low",
        ).value.strip() or "low"
        prewarm_classifier(
            run_label="manager-frontdoor-classify",
            model=resolve_manager_classify_model(
                backend=classifier_backend_name,
            ),
            reasoning_effort=classify_effort,
            lean=True,
            cwd=cwd,
            front_door_session=True,
        )
        prewarm_reply(
            run_label="simple-1",
            model=resolve_manager_reply_model(backend=reply_backend_name),
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_SELF_REASONING_EFFORT",
                default="high",
            ),
            lean=False,
            cwd=cwd,
            add_dirs=(
                [str(mem.project_root)]
                if str(mem.project_root) != cwd
                else None
            ),
        )
        if not _is_manager_prewarm_owner(sid):
            _release_manager_state(sid)
            return
        state["_manager_acp_prewarmed"] = True


def _is_manager_prewarm_owner(sid: str) -> bool:
    with _MANAGER_PREWARMING_LOCK:
        return _MANAGER_PREWARM_OWNER == sid


def _claim_manager_prewarm_owner(sid: str) -> None:
    """Make the latest explicit active-project request the sole prewarm owner."""
    global _MANAGER_PREWARM_OWNER

    with _MANAGER_PREWARMING_LOCK:
        _MANAGER_PREWARM_OWNER = sid


def _mark_manager_activity(sid: str) -> None:
    _claim_manager_prewarm_owner(sid)


def schedule_manager_prewarm(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> None:
    """Best-effort prewarm for the one project currently open in the Web UI."""
    _claim_manager_prewarm_owner(sid)
    state = _STATES.get(sid)
    if state and state.get("_manager_acp_prewarmed"):
        return
    with _MANAGER_PREWARMING_LOCK:
        if sid in _MANAGER_PREWARMING:
            return
        _MANAGER_PREWARMING.add(sid)

    def _run() -> None:
        try:
            _prewarm_manager_context(sid, global_root=global_root)
        except Exception:  # noqa: BLE001 - page reads must stay available
            pass
        finally:
            with _MANAGER_PREWARMING_LOCK:
                _MANAGER_PREWARMING.discard(sid)

    threading.Thread(
        target=_run,
        name=f"manager-prewarm-{sid}",
        daemon=True,
    ).start()


def _warm_context_limits() -> tuple[int, float]:
    import os

    try:
        limit = max(
            1,
            int(
                os.environ.get("ARGUS_SKILL_MANAGER_WARM_CONTEXT_LIMIT", "")
                or _DEFAULT_WARM_CONTEXT_LIMIT
            ),
        )
    except ValueError:
        limit = _DEFAULT_WARM_CONTEXT_LIMIT
    try:
        idle = max(
            60.0,
            float(
                os.environ.get("ARGUS_SKILL_MANAGER_WARM_CONTEXT_IDLE_SECONDS", "")
                or _DEFAULT_WARM_CONTEXT_IDLE_SECONDS
            ),
        )
    except ValueError:
        idle = float(_DEFAULT_WARM_CONTEXT_IDLE_SECONDS)
    return limit, idle


def _evict_stale_manager_states(*, exclude_sid: str) -> None:
    """Bound warm ACP processes without interrupting an active Manager turn."""
    now = time.monotonic()
    limit, idle_seconds = _warm_context_limits()
    ordered = sorted(
        (
            (sid, float(state.get("last_access_monotonic") or 0.0))
            for sid, state in list(_STATES.items())
            if sid != exclude_sid
        ),
        key=lambda row: row[1],
    )
    stale = [sid for sid, touched in ordered if now - touched >= idle_seconds]
    overflow = max(0, len(_STATES) - limit + (0 if exclude_sid in _STATES else 1))
    candidates = list(dict.fromkeys([*stale, *(sid for sid, _ in ordered[:overflow])]))
    for sid in candidates:
        lock = _lock_for(sid)
        if not lock.acquire(blocking=False):
            continue
        try:
            _release_manager_state(sid)
        finally:
            lock.release()


def _chat_state_for(
    sid: str,
    *,
    manager_activity: bool = True,
) -> dict[str, Any]:
    if manager_activity:
        _mark_manager_activity(sid)
    _evict_stale_manager_states(exclude_sid=sid)
    st = _STATES.get(sid)
    if st is not None:
        if manager_activity:
            st["_manager_activity_seen"] = True
        st["last_access_monotonic"] = time.monotonic()
        return st
    from ..agent_cli.runner_backend import normalize_runner_backend
    from ..core.knobs import resolve_role_backend
    from ..manager.dispatch import DEFAULT_MANAGER_CONFIG

    try:
        # default="codex": this seeds a per-session display/default field on a
        # web chat state that must be constructible on any host. The `except`
        # now also catches normalize_runner_backend's ValueError on a typo'd
        # knob and a corrupt knob store — log.exception so those two stop being
        # byte-identical to "the operator really chose codex".
        backend = normalize_runner_backend(
            resolve_role_backend("manager", default="codex")
        )
    except Exception:  # noqa: BLE001 — a chat session must still open
        log.exception("manager_state: falling back to codex for session %s", sid)
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
        "last_access_monotonic": time.monotonic(),
        "_manager_activity_seen": manager_activity,
        "mission_count": 0,
        "config": dict(DEFAULT_MANAGER_CONFIG),
        "continuous_objective": "",
    }
    _STATES[sid] = st
    return st


def _rotate_after() -> int:
    """Turns before the Manager session is rotated (a proxy for its context
    filling). Override with ARGUS_SKILL_MANAGER_ROTATE_TURNS."""
    import os

    try:
        return max(4, int(os.environ.get("ARGUS_SKILL_MANAGER_ROTATE_TURNS", "40")))
    except ValueError:
        return 40


def reset_manager_context(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> bool:
    """Drop the warm Manager conversation while preserving project state."""
    from ..manager import reset_manager_session

    root = Path(global_root) if global_root else None
    life_dir = core_paths.session_state_root(sid, root=root)
    if not life_dir.is_dir():
        return False
    with _lock_for(sid):
        _release_manager_state(sid)
        reset_manager_session(life_dir)
    return True


def shutdown_manager_bridge() -> None:
    """Release warm Manager runners and Copilot ACP children on Web shutdown."""
    global _MANAGER_PREWARM_OWNER

    with _REGISTRY_LOCK:
        states = list(_STATES.values())
        _STATES.clear()
        _LOCKS.clear()
        _CONTROL_GENERATIONS.clear()
    with _MANAGER_PREWARMING_LOCK:
        _MANAGER_PREWARMING.clear()
        _MANAGER_PREWARM_OWNER = None
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
