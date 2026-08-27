"""Persisted, observable source-update jobs for the local Web cockpit."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..apps.update import UpdateError, inspect_source_checkout, update_source_checkout
from ..core.runtime_identity import runtime_identity, source_root

STATUS_FILE = "source-update.json"
_THREADS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def _status_path(global_root: Path | str) -> Path:
    return Path(global_root).expanduser().resolve() / STATUS_FILE


def _write_status(global_root: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _status_path(global_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 1, **payload, "updated_at": time.time()}
    fd, tmp_name = tempfile.mkstemp(prefix=".source-update-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
    return value


def _initial_status() -> dict[str, Any]:
    identity = runtime_identity()
    worktree = identity.get("worktree") or {}
    now = time.time()
    return {
        "schema_version": 1,
        "state": "idle",
        "phase": "idle",
        "running": False,
        "source_root": str(source_root()),
        "upstream": "lbx154/Argus/main",
        "current_revision": str(identity.get("revision") or ""),
        "upstream_revision": "",
        "branch": str(worktree.get("branch") or ""),
        "dirty": worktree.get("dirty"),
        "can_update": bool(worktree.get("branch")) and worktree.get("dirty") is False,
        "update_available": None,
        "changed": False,
        "restart_required": False,
        "message": "Check for an upstream revision or pull the latest version.",
        "error": "",
        "started_at": None,
        "checked_at": None,
        "updated_at": now,
    }


def read_source_update_status(global_root: Path | str) -> dict[str, Any]:
    path = _status_path(global_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _initial_status()
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return _initial_status()
    return payload


def _merge_status(global_root: Path | str, **changes: Any) -> dict[str, Any]:
    return _write_status(global_root, {**read_source_update_status(global_root), **changes})


def _run_source_update(
    global_root: Path | str,
    action: str,
    *,
    checkout: Path | None = None,
) -> None:
    root = (checkout or source_root()).expanduser().resolve()
    try:
        _merge_status(global_root, state="checking", phase="checking", running=True)
        check = inspect_source_checkout(root)
        common = {
            "source_root": str(check.root),
            "upstream": check.upstream,
            "current_revision": check.current_revision,
            "upstream_revision": check.upstream_revision,
            "branch": check.branch,
            "dirty": check.dirty,
            "can_update": check.can_update,
            "update_available": check.update_available,
            "checked_at": time.time(),
        }
        if action == "check":
            _merge_status(
                global_root,
                **common,
                state="available" if check.update_available else "current",
                phase="complete",
                running=False,
                changed=False,
                restart_required=False,
                message=(
                    "Version check complete. Local changes block source updates."
                    if check.dirty
                    else "A newer public main revision is available."
                    if check.update_available
                    else "Argus is already on the latest public main revision."
                ),
                error=(
                    "Source checkout has local changes; commit or stash them before updating."
                    if check.dirty
                    else ""
                ),
            )
            return
        if not check.can_update:
            if check.dirty:
                raise UpdateError(
                    "source checkout has local changes; commit, stash, or remove them before updating"
                )
            raise UpdateError("source checkout is detached; switch to a branch first")

        def progress(phase: str) -> None:
            state = "checking" if phase == "validating" else "updating"
            _merge_status(global_root, **common, state=state, phase=phase, running=True)

        result = update_source_checkout(
            root,
            python_executable=sys.executable,
            on_progress=progress,
        )
        _merge_status(
            global_root,
            **{
                **common,
                "state": "succeeded",
                "phase": "complete",
                "running": False,
                "current_revision": result.after_revision,
                "upstream_revision": result.after_revision,
                "update_available": False,
                "changed": result.changed,
                "restart_required": result.changed,
                "message": (
                    "Latest source installed. Restart the cockpit and safely reload active daemons."
                    if result.changed
                    else "Argus is already on the latest public main revision."
                ),
                "error": "",
            },
        )
    except Exception as exc:  # noqa: BLE001 - failure is persisted for the UI
        _merge_status(
            global_root,
            state="failed",
            phase="failed",
            running=False,
            message="The update did not change the installed source.",
            error=str(exc)[:1000],
        )
    finally:
        with _LOCK:
            _THREADS.pop(str(_status_path(global_root)), None)


def start_source_update(
    global_root: Path | str,
    *,
    action: str,
) -> dict[str, Any]:
    if action not in {"check", "apply"}:
        raise ValueError("source update action must be 'check' or 'apply'")
    key = str(_status_path(global_root))
    with _LOCK:
        active = _THREADS.get(key)
        if active is not None and active.is_alive():
            return read_source_update_status(global_root)
        status = _write_status(
            global_root,
            {
                **read_source_update_status(global_root),
                "state": "checking" if action == "check" else "updating",
                "phase": "queued",
                "running": True,
                "changed": False,
                "restart_required": False,
                "message": (
                    "Checking public main…"
                    if action == "check"
                    else "Preparing to pull public main…"
                ),
                "error": "",
                "started_at": time.time(),
            },
        )
        thread = threading.Thread(
            target=_run_source_update,
            args=(global_root, action),
            name=f"argus-source-update-{action}",
            daemon=True,
        )
        _THREADS[key] = thread
        thread.start()
        return status


__all__ = ["read_source_update_status", "start_source_update"]
