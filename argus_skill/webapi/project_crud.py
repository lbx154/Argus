"""Project/session CRUD operations for the webapi server.

Extracted from ``server.py`` as part of a behavior-preserving decomposition.
Public names remain re-exported from ``server`` for backward compatibility.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..core.session import (
    SessionMeta,
    normalize_session_name,
    session_lifecycle_lock,
    session_meta_lock,
    update_session_meta,
)
from ..daemon.life_worker import read_continuous_state
from . import project_state

_global_root = project_state.resolve_global_root
project_life_dir = project_state.project_life_dir


def _srv():
    """Lazily resolve the ``server`` module so tests that monkeypatch
    ``server.read_daemon_status`` still take effect for this module's
    internal calls, matching pre-split monkeypatch semantics.
    """
    from . import server

    return server


def update_project(
    sid: str,
    *,
    name: str,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Update operator-owned session metadata without changing mission state."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    try:
        objective = read_continuous_state(life_dir).objective
    except Exception:  # noqa: BLE001 — legacy metadata repair is best-effort
        objective = ""
    normalized_name = normalize_session_name(name)

    def _rename(meta: SessionMeta) -> None:
        now = time.time()
        if not meta.created:
            meta.created = now
        if not meta.last_active:
            meta.last_active = now
        if not meta.cwd:
            meta.cwd = str(life_dir)
        if not meta.objective:
            meta.objective = objective
        meta.display_name = normalized_name

    meta = update_session_meta(root, sid, _rename, create=True)
    if meta is None:
        return None
    return {"ok": True, "sid": sid, "name": meta.display_name}


def delete_project(
    sid: str,
    *,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Reversibly remove a stopped session by moving it to projects_trash."""
    from .manager_bridge import manager_context_lock, release_manager_context

    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with manager_context_lock(sid):
        with session_lifecycle_lock(lock_root, sid):
            with session_meta_lock(root, sid):
                life_dir = project_life_dir(sid, global_root=root)
                if life_dir is None:
                    return None
                status = _srv().read_daemon_status(life_dir)
                if status.alive:
                    return {
                        "ok": False,
                        "sid": sid,
                        "error": "pause the daemon before deleting this session",
                    }

                date = time.strftime("%Y%m%d", time.localtime())
                dest_parent = root / "projects_trash" / date
                dest_parent.mkdir(parents=True, exist_ok=True)
                dest = dest_parent / sid
                if dest.exists():
                    dest = dest_parent / f"{sid}.{int(time.time())}"
                shutil.move(str(life_dir), str(dest))
                release_manager_context(sid)
                return {
                    "ok": True,
                    "sid": sid,
                    "trash_path": str(dest.relative_to(root)),
                }


def list_trashed_projects(
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    root = _global_root(global_root)
    trash_root = root / "projects_trash"
    out: list[dict[str, Any]] = []
    try:
        candidates = [
            path
            for date_dir in trash_root.iterdir()
            if date_dir.is_dir()
            for path in date_dir.iterdir()
            if path.is_dir()
        ]
    except OSError:
        candidates = []
    for path in candidates:
        payload: dict[str, Any] = {}
        try:
            value = json.loads((path / "session.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                payload = value
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        sid = str(payload.get("id") or path.name.split(".", 1)[0]).strip()
        label = str(payload.get("display_name") or payload.get("objective") or sid).strip()
        try:
            trashed_at = path.stat().st_mtime
        except OSError:
            trashed_at = 0.0
        out.append(
            {
                "sid": sid,
                "label": label or sid,
                "launch_cwd": str(payload.get("launch_cwd") or ""),
                "trash_path": str(path.relative_to(root)),
                "trashed_at": trashed_at,
            }
        )
    out.sort(key=lambda row: float(row.get("trashed_at") or 0.0), reverse=True)
    return out


def restore_trashed_project(
    trash_path: str,
    *,
    global_root: Path | str | None = None,
    existing_roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    root = _global_root(global_root).resolve()
    trash_root = (root / "projects_trash").resolve()
    try:
        source = (root / trash_path).resolve()
    except (OSError, ValueError):
        return None
    try:
        relative = source.relative_to(trash_root)
    except ValueError:
        return None
    if (
        len(relative.parts) != 2
        or len(relative.parts[0]) != 8
        or not relative.parts[0].isdigit()
        or source.is_symlink()
        or source.parent.is_symlink()
        or not source.is_dir()
    ):
        return None
    payload: dict[str, Any] = {}
    try:
        value = json.loads((source / "session.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            payload = value
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    sid = str(payload.get("id") or source.name.split(".", 1)[0]).strip()
    if not sid or Path(sid).name != sid:
        return None
    destination = (root / "projects" / sid).resolve()
    if destination.parent != (root / "projects").resolve():
        return None
    roots_to_check = tuple(existing_roots or (root,))
    lock_root = _global_root(roots_to_check[0])
    with session_lifecycle_lock(lock_root, sid):
        if not source.is_dir():
            return None
        if any(
            project_life_dir(sid, global_root=candidate) is not None for candidate in roots_to_check
        ):
            return {
                "ok": False,
                "sid": sid,
                "error": "a live session with this id already exists",
            }
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return {"ok": True, "sid": sid}


def set_continuous(
    sid: str,
    *,
    enabled: bool,
    objective: str = "",
    global_root: Path | str | None = None,
) -> bool | None:
    """Start/stop this project's continuous (self-directed) campaign by writing
    the hot-reloadable ``continuous.json``."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    if not enabled:
        from .manager_bridge import disable_manager_continuous

        disable_manager_continuous(sid, life_dir=life_dir)
        return True
    from .manager_bridge import manager_continuous_handoff

    manager_continuous_handoff(
        sid,
        objective.strip(),
        global_root=global_root,
    )
    return True


# ---------------------------------------------------------------------------
# Wave-1 read/inspect + backlog-lifecycle helpers — 1:1 with the Python
# cockpit's /status /journal /note /doctor /config /identity /transcript and
# the /done /skip /rm /stop backlog commands. All delegate; fail-soft per part.
# ---------------------------------------------------------------------------
