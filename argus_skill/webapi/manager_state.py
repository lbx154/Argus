"""Manager chat-state, locks, and prewarm lifecycle for the webapi bridge.

Extracted from ``manager_bridge.py`` as part of a behavior-preserving
decomposition. Owns the per-project chat_state cache, the per-sid lock
registry, and the manager-runner prewarm bookkeeping so
``manager_bridge.py`` can stay focused on the ``manager_message`` /
``manager_plan`` request pipeline. Public names are re-exported from
``manager_bridge`` unchanged so existing imports/monkeypatches keep working.
"""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core import paths as core_paths

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
_DEFAULT_WARM_CONTEXT_LIMIT = 8
_DEFAULT_WARM_CONTEXT_IDLE_SECONDS = 30 * 60


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
            os.environ.get("ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "low").strip() or "low"
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
            read_only=True,
            add_dirs=([str(mem.project_root)] if str(mem.project_root) != cwd else None),
        )
        state["_manager_acp_prewarmed"] = True


def _manager_context_is_prewarmed(sid: str) -> bool:
    with _lock_for(sid):
        state = _STATES.get(sid)
        return bool(state and state.get("_manager_acp_prewarmed"))


def schedule_manager_prewarm(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> None:
    """Warm exactly one project's private Manager ACP pool in background."""
    if _manager_context_is_prewarmed(sid):
        return
    with _MANAGER_PREWARMING_LOCK:
        if sid in _MANAGER_PREWARMING:
            return
        if _manager_context_is_prewarmed(sid):
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
        "last_access_monotonic": time.monotonic(),
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
    life_dir = core_paths.session_state_root(sid, root=root) if root is not None else None
    if life_dir is None:
        life_dir = core_paths.session_state_root(sid)
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
