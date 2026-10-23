"""Manager chat-state and locks for the webapi bridge.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Owns the per-project chat_state cache and the per-sid lock
registry so ``manager_bridge.py`` can stay focused on the ``manager_message`` /
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
from itertools import count
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
# Emergency natural-language pause bypasses the per-session Manager lock. A
# generation bump lets any older turn notice that it was superseded before it
# can commit/dispatch work after the operator has clocked the session out.
_CONTROL_GENERATIONS: dict[str, int] = {}
_CONTROL_SEQUENCE = count(1)
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
        if sid not in _CONTROL_GENERATIONS:
            _CONTROL_GENERATIONS[sid] = next(_CONTROL_SEQUENCE)
        return _CONTROL_GENERATIONS[sid]


def interrupt_manager_turns(
    sid: str, *, clear_continuous: bool = True, expected_generation: int | None = None,
) -> int:
    """Supersede older Manager turns without waiting for their session lock.

    Persistent daemon/continuous state is changed by the pause handler. This
    in-process fence only prevents an already-running front-door turn from
    dispatching stale work after that durable pause lands.
    """
    with _REGISTRY_LOCK:
        if expected_generation is not None and _CONTROL_GENERATIONS.get(sid, 0) != expected_generation:
            from ..manager.front_door import ManagerHandoffSupersededError

            raise ManagerHandoffSupersededError("A newer control request superseded this request")
        # A recreated/evicted context must never reuse an older request's token.
        generation = next(_CONTROL_SEQUENCE)
        _CONTROL_GENERATIONS[sid] = generation
        state = _STATES.get(sid)
        if state is not None and clear_continuous:
            state.setdefault("config", {})["continuous"] = False
            state["continuous_objective"] = ""
        return generation


def sync_manager_continuous(sid: str, generation: int, objective: str) -> None:
    """Publish the committed view only while this request still owns its generation."""
    with _REGISTRY_LOCK:
        if _CONTROL_GENERATIONS.get(sid, 0) != generation:
            from ..manager.front_door import ManagerHandoffSupersededError

            raise ManagerHandoffSupersededError("A newer control request superseded this result")
        state = _STATES.get(sid)
        if state is not None:
            state.setdefault("config", {})["continuous"] = True
            state["continuous_objective"] = objective


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
            if hasattr(runner, "reset_chat_session"):
                runner.reset_chat_session()
        except Exception:  # noqa: BLE001
            pass


def release_manager_context(sid: str) -> None:
    """Release one warm Manager runner without touching project files."""
    with _lock_for(sid):
        _release_manager_state(sid)


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
    """Bound cached Manager runners without interrupting an active Manager turn."""
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


def _chat_state_for(sid: str) -> dict[str, Any]:
    _evict_stale_manager_states(exclude_sid=sid)
    st = _STATES.get(sid)
    if st is not None:
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
        "session_started_s": time.monotonic(),
        "last_access_monotonic": time.monotonic(),
        "mission_count": 0,
        "config": dict(DEFAULT_MANAGER_CONFIG),
        "continuous_objective": "",
    }
    _STATES[sid] = st
    return st


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
    """Release cached Manager runners on Web shutdown."""
    from ..manager.supervision import shutdown_supervision

    shutdown_supervision()
    with _REGISTRY_LOCK:
        states = list(_STATES.values())
        _STATES.clear()
        _LOCKS.clear()
        _CONTROL_GENERATIONS.clear()
    for state in states:
        runner = state.get("manager_runner")
        if runner is not None and hasattr(runner, "reset_chat_session"):
            try:
                runner.reset_chat_session()
            except Exception:  # noqa: BLE001
                pass
