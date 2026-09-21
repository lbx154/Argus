"""Execution-only sticky pause and provider leases. No billing reads or writes.

No expiry, watcher revision, budget policy, PID death or prose clears a fence.
This safety release intentionally offers no unpause/repair API.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import portalocker

from .json_codec import is_finite_number
from .safety_io import durable_json, loads_strict_json


class DispatchSafetyError(RuntimeError):
    """Execution permission is absent or its persisted evidence is invalid."""
    def __init__(self, path: Path, offset: int, raw: bytes, reason: str):
        super().__init__(f"dispatch safety: {path.name}: {reason}")

SAFETY_FILE = "dispatch-safety.json"


def _json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise DispatchSafetyError(path, 0, b"", "symlink in safety state")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DispatchSafetyError(path, 0, b"", "unreadable safety state") from exc
    try:
        row = loads_strict_json(raw)
        if not isinstance(row, dict):
            raise ValueError("not an object")
        return row
    except (ValueError, UnicodeError) as exc:
        raise DispatchSafetyError(path, 0, raw, "invalid safety state") from exc


def safety_snapshot(project: Path) -> dict[str, Any]:
    data = _json(project / SAFETY_FILE)
    if data is None:
        return {"version": 1, "paused": False, "epoch": 0, "audit": []}
    try:
        if (type(data.get("version")) is not int or data["version"] != 1
                or data.get("paused") is not True or type(data.get("epoch")) is not int
                or data["epoch"] < 1 or not isinstance(data.get("audit"), list)
                or len(data["audit"]) != data["epoch"]):
            raise ValueError("invalid fence schema")
        for epoch, event in enumerate(data["audit"], 1):
            if (not isinstance(event, dict) or event.get("action") != "quiesce"
                    or type(event.get("epoch")) is not int or event["epoch"] != epoch
                    or type(event.get("from_epoch")) is not int or event["from_epoch"] != epoch-1
                    or not isinstance(event.get("reason"), str) or not event["reason"].strip()
                    or event.get("preserve_claims_and_debt") is not True
                    or type(event.get("owner_pid")) is not int or event["owner_pid"] <= 0):
                raise ValueError("invalid fence audit")
            if event.get("at") is None:
                raise ValueError("missing fence audit timestamp")
            if not is_finite_number(event["at"]) or event["at"] < 0:
                raise ValueError("invalid fence audit timestamp")
        if data.get("reason") != data["audit"][-1]["reason"]:
            raise ValueError("fence reason/audit mismatch")
    except (ValueError, TypeError) as exc:
        raise DispatchSafetyError(project / SAFETY_FILE, 0, b"", str(exc)) from exc
    return data


def assert_project_dispatch(project: Path | None) -> None:
    if project is not None:
        if safety_snapshot(project)["paused"]:
            raise DispatchSafetyError(project / SAFETY_FILE, 0, b"", "project dispatch PAUSED")


@contextmanager
def _pause_lock(project: Path):
    # Separate from both the cost-state lock and the in-flight execution lease.
    # A damaged/missing accounting state must never prevent explicit quiesce.
    project.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(project / "dispatch-state.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        portalocker.lock(fd, portalocker.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextmanager
def provider_dispatch_guard(project: Path | None):
    """Hold a shared execution lease; quiesce reports active work without killing it."""
    if project is None:
        yield
        return
    project.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(project / "dispatch-execution.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        portalocker.lock(fd, portalocker.LOCK_SH)
        assert_project_dispatch(project)
        yield
    finally:
        os.close(fd)


def quiesce_project(*, root: Path, project: Path, expected_epoch: int, reason: str) -> dict[str, Any]:
    """Persist an audited CAS fence, without stop signals or accounting writes.

    The fence is durable before the execution-lease observation. Previously
    admitted calls may finish; no next round can acquire a new guarded lease.
    A busy lease means quiescing, not stopped. Owners/claims/debt are untouched.
    """
    from .dispatch_ownership import _state_path
    # API/server-selected ownership is independent of this process's caller env.
    project = _state_path(project, root.expanduser().resolve())
    if type(expected_epoch) is not int or expected_epoch < 0:
        raise ValueError("epoch must be a non-negative integer")
    if not reason.strip() or len(reason) > 1000:
        raise ValueError("a bounded reason is required")
    with _pause_lock(project):
        state = safety_snapshot(project)
        if state["epoch"] != expected_epoch:
            raise ValueError("stale dispatch safety epoch")
        state = {**state, "paused": True, "epoch": expected_epoch + 1,
                 "reason": reason, "audit": [*state["audit"], {
                     "action": "quiesce", "from_epoch": expected_epoch,
                     "epoch": expected_epoch + 1, "reason": reason, "at": time.time(),
                     "owner_pid": os.getpid(), "preserve_claims_and_debt": True}]}
        durable_json(project / SAFETY_FILE, state)
    fd = os.open(str(project / "dispatch-execution.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    active = False
    try:
        try:
            portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            active = True
    finally:
        os.close(fd)
    return {**state, "quiescent": not active, "daemon_stopped": False,
            "accounting_settled": False, "claims_transferred": False}
