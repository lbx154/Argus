"""Durable idempotent command protocol for daemon lifecycle operations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from ..core.event_catalog import EventType
from ..core.file_lock import exclusive_file_lock
from ..core.metrics import metrics_root_for_project, record_metric

COMMAND_LOG_FILE = "daemon.commands.jsonl"
COMMAND_STATE_FILE = "daemon.command-state.json"
COMMAND_LOCK_FILE = "daemon.command-state.lock"
COMMAND_EXEC_LOCK_FILE = "daemon.command-exec.lock"
COMMAND_SCHEMA_VERSION = 1
COMMAND_OPERATIONS = frozenset(
    {"create", "start", "stop", "drain", "kill", "replace", "upgrade"}
)
CommandStatus = Literal["accepted", "running", "applied", "failed", "rejected"]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_MAX_COMMAND_HISTORY = 1_000
_COMMAND_LOCK_TIMEOUT_SECONDS = 30.0


class DaemonCommandStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class DaemonCommandReceipt:
    command_id: str
    operation: str
    status: CommandStatus
    revision: int
    expected_revision: int | None
    args: dict[str, Any]
    result: dict[str, Any]
    error: str
    submitted_at: float
    updated_at: float

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "operation": self.operation,
            "status": self.status,
            "revision": self.revision,
            "expected_revision": self.expected_revision,
            "args": dict(self.args),
            "result": dict(self.result),
            "error": self.error,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
        }


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "revision": 0,
        "commands": {},
    }


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / COMMAND_LOCK_FILE
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(timeout=_COMMAND_LOCK_TIMEOUT_SECONDS):
        raise TimeoutError("timed out acquiring daemon command state thread lock")
    fd: int | None = None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a+", encoding="utf-8", closefd=False) as handle:
            with exclusive_file_lock(
                handle,
                timeout_seconds=_COMMAND_LOCK_TIMEOUT_SECONDS,
                lock_name=f"daemon command state lock {path}",
            ):
                yield
    finally:
        if fd is not None:
            os.close(fd)
        lock.release()


@contextmanager
def _execution_lock(root: Path, *, blocking: bool) -> Iterator[bool]:
    path = root / COMMAND_EXEC_LOCK_FILE
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    acquired_thread = lock.acquire(
        blocking=blocking,
        timeout=_COMMAND_LOCK_TIMEOUT_SECONDS if blocking else -1,
    ) if blocking else lock.acquire(blocking=False)
    if not acquired_thread:
        yield False
        return
    fd: int | None = None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a+", encoding="utf-8", closefd=False) as handle:
            file_lock = exclusive_file_lock(
                handle,
                timeout_seconds=(
                    _COMMAND_LOCK_TIMEOUT_SECONDS if blocking else 0.0
                ),
                lock_name=f"daemon command execution lock {path}",
            )
            try:
                file_lock.__enter__()
            except TimeoutError:
                yield False
                return
            try:
                yield True
            finally:
                file_lock.__exit__(None, None, None)
    finally:
        if fd is not None:
            os.close(fd)
        lock.release()


@contextmanager
def daemon_command_execution_lock(
    root: Path | str,
    *,
    blocking: bool = True,
) -> Iterator[bool]:
    """Serialize lifecycle work with every CLI/Web daemon command."""
    with _execution_lock(Path(root).expanduser(), blocking=blocking) as acquired:
        yield acquired


def _read_state(root: Path) -> dict[str, Any]:
    path = root / COMMAND_STATE_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_state()
    except (OSError, json.JSONDecodeError) as exc:
        raise DaemonCommandStateError(f"cannot read command state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DaemonCommandStateError(f"invalid command state {path}: expected object")
    if payload.get("schema_version") != COMMAND_SCHEMA_VERSION:
        raise DaemonCommandStateError(
            f"unsupported command state schema {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("commands"), dict):
        raise DaemonCommandStateError(f"invalid command state {path}: commands must be object")
    try:
        revision = int(payload.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise DaemonCommandStateError(f"invalid command state {path}: revision") from exc
    return {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "revision": max(0, revision),
        "commands": dict(payload["commands"]),
    }


def _write_state(root: Path, state: dict[str, Any]) -> None:
    commands = state.get("commands") or {}
    if len(commands) > _MAX_COMMAND_HISTORY:
        ordered = sorted(
            commands.items(),
            key=lambda item: float(item[1].get("updated_at") or 0.0),
            reverse=True,
        )[:_MAX_COMMAND_HISTORY]
        state["commands"] = dict(ordered)
    root.mkdir(parents=True, exist_ok=True)
    target = root / COMMAND_STATE_FILE
    fd, tmp_name = tempfile.mkstemp(prefix=".daemon-command-state-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return repr(value)


def _receipt(row: dict[str, Any]) -> DaemonCommandReceipt:
    return DaemonCommandReceipt(
        command_id=str(row.get("command_id") or ""),
        operation=str(row.get("operation") or ""),
        status=str(row.get("status") or "failed"),  # type: ignore[arg-type]
        revision=int(row.get("revision") or 0),
        expected_revision=(
            int(row["expected_revision"])
            if row.get("expected_revision") is not None
            else None
        ),
        args=dict(row.get("args") or {}),
        result=dict(row.get("result") or {}),
        error=str(row.get("error") or ""),
        submitted_at=float(row.get("submitted_at") or 0.0),
        updated_at=float(row.get("updated_at") or 0.0),
    )


def _append_command(root: Path, row: dict[str, Any]) -> None:
    try:
        with (root / COMMAND_LOG_FILE).open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    except OSError:
        pass


def _emit(root: Path, event_type: EventType, row: dict[str, Any]) -> None:
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=root).append({
            "type": event_type,
            "command_id": row["command_id"],
            "operation": row["operation"],
            "status": row["status"],
            "revision": row["revision"],
            "expected_revision": row.get("expected_revision"),
            "result": row.get("result") or {},
            "error": row.get("error") or "",
        })
    except Exception:  # noqa: BLE001 - command state remains authoritative
        pass


def submit_daemon_command(
    root: Path | str,
    *,
    operation: str,
    args: dict[str, Any] | None = None,
    command_id: str | None = None,
    expected_revision: int | None = None,
    issuer: str = "",
    now: float | None = None,
) -> DaemonCommandReceipt:
    path = Path(root).expanduser()
    op = str(operation or "").strip().lower()
    if op not in COMMAND_OPERATIONS:
        raise ValueError(f"unsupported daemon command operation: {operation!r}")
    cid = str(command_id or uuid.uuid4().hex).strip()
    if not cid or len(cid) > 128:
        raise ValueError("command_id must be 1-128 characters")
    timestamp = time.time() if now is None else float(now)
    with _locked(path):
        state = _read_state(path)
        existing = state["commands"].get(cid)
        if isinstance(existing, dict):
            return _receipt(existing)
        current_revision = int(state["revision"])
        rejected = (
            expected_revision is not None
            and int(expected_revision) != current_revision
        )
        revision = current_revision + 1
        row = {
            "schema_version": COMMAND_SCHEMA_VERSION,
            "command_id": cid,
            "operation": op,
            "status": "rejected" if rejected else "accepted",
            "revision": revision,
            "expected_revision": expected_revision,
            "args": _jsonable(args or {}),
            "result": {},
            "error": (
                f"stale command revision: expected {expected_revision}, "
                f"current {current_revision}"
                if rejected
                else ""
            ),
            "issuer": str(issuer or ""),
            "submitted_at": timestamp,
            "updated_at": timestamp,
        }
        state["revision"] = revision
        state["commands"][cid] = row
        _write_state(path, state)
    _append_command(path, row)
    _emit(
        path,
        EventType.DAEMON_COMMAND_REJECTED if rejected else EventType.DAEMON_COMMAND_SUBMITTED,
        row,
    )
    if rejected:
        record_metric(
            metrics_root_for_project(path),
            "daemon.command",
            labels={"operation": op, "status": "rejected"},
            fields={"command_id": cid, "revision": revision, "error": row["error"]},
        )
    return _receipt(row)


def claim_daemon_command(
    root: Path | str,
    command_id: str,
    *,
    reclaim_running: bool = False,
) -> bool:
    path = Path(root).expanduser()
    with _locked(path):
        state = _read_state(path)
        row = state["commands"].get(command_id)
        if not isinstance(row, dict):
            return False
        status = row.get("status")
        if status == "running":
            if not reclaim_running:
                return False
        elif status != "accepted":
            return False
        state["revision"] = int(state["revision"]) + 1
        row["status"] = "running"
        row["revision"] = state["revision"]
        row["owner_pid"] = os.getpid()
        row["running_at"] = time.time()
        row["updated_at"] = row["running_at"]
        state["commands"][command_id] = row
        _write_state(path, state)
    return True


def ack_daemon_command(
    root: Path | str,
    command_id: str,
    *,
    status: Literal["applied", "failed"],
    result: dict[str, Any] | None = None,
    error: str = "",
) -> DaemonCommandReceipt:
    path = Path(root).expanduser()
    with _locked(path):
        state = _read_state(path)
        row = state["commands"].get(command_id)
        if not isinstance(row, dict):
            raise KeyError(command_id)
        if row.get("status") in {"applied", "failed", "rejected"}:
            return _receipt(row)
        state["revision"] = int(state["revision"]) + 1
        row["status"] = status
        row["revision"] = state["revision"]
        row["result"] = _jsonable(result or {})
        row["error"] = str(error or "")[:2_000]
        row["updated_at"] = time.time()
        state["commands"][command_id] = row
        _write_state(path, state)
    _emit(path, EventType.DAEMON_COMMAND_COMPLETED, row)
    record_metric(
        metrics_root_for_project(path),
        "daemon.command",
        labels={"operation": row["operation"], "status": status},
        fields={
            "command_id": command_id,
            "revision": row["revision"],
            "error": row["error"],
        },
    )
    return _receipt(row)


def command_status(root: Path | str, command_id: str) -> DaemonCommandReceipt | None:
    path = Path(root).expanduser()
    with _locked(path):
        row = _read_state(path)["commands"].get(command_id)
    return _receipt(row) if isinstance(row, dict) else None


def execute_daemon_command(
    root: Path | str,
    *,
    operation: str,
    handler: Callable[[], dict[str, Any]],
    args: dict[str, Any] | None = None,
    command_id: str | None = None,
    expected_revision: int | None = None,
    issuer: str = "",
) -> DaemonCommandReceipt:
    receipt = submit_daemon_command(
        root,
        operation=operation,
        args=args,
        command_id=command_id,
        expected_revision=expected_revision,
        issuer=issuer,
    )
    if receipt.status in {"applied", "failed", "rejected"}:
        return receipt
    path = Path(root).expanduser()
    with daemon_command_execution_lock(
        path,
        blocking=receipt.status != "running",
    ) as acquired:
        if not acquired:
            return command_status(root, receipt.command_id) or receipt
        current = command_status(root, receipt.command_id) or receipt
        if current.status in {"applied", "failed", "rejected"}:
            return current
        if not claim_daemon_command(
            root,
            receipt.command_id,
            reclaim_running=True,
        ):
            return command_status(root, receipt.command_id) or current
        try:
            result = handler()
        except Exception as exc:  # noqa: BLE001 - persist failure ACK, then return
            return ack_daemon_command(
                root,
                receipt.command_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(result, dict):
            return ack_daemon_command(
                root,
                receipt.command_id,
                status="failed",
                error="daemon command handler returned no result",
            )
        try:
            rc = int(result.get("rc") or 0)
        except (TypeError, ValueError):
            rc = 2
        if rc != 0:
            return ack_daemon_command(
                root,
                receipt.command_id,
                status="failed",
                result=result,
                error=str(result.get("error") or f"{operation} returned rc={rc}"),
            )
        return ack_daemon_command(
            root,
            receipt.command_id,
            status="applied",
            result=result,
        )


def daemon_command_snapshot(root: Path | str) -> dict[str, Any]:
    path = Path(root).expanduser()
    with _locked(path):
        state = _read_state(path)
    recent = sorted(
        state["commands"].values(),
        key=lambda row: float(row.get("updated_at") or 0.0),
        reverse=True,
    )[:20]
    return {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "revision": int(state["revision"]),
        "recent": [_receipt(row).to_jsonable() for row in recent],
    }


__all__ = [
    "COMMAND_LOCK_FILE",
    "COMMAND_EXEC_LOCK_FILE",
    "COMMAND_LOG_FILE",
    "COMMAND_OPERATIONS",
    "COMMAND_SCHEMA_VERSION",
    "COMMAND_STATE_FILE",
    "DaemonCommandReceipt",
    "DaemonCommandStateError",
    "ack_daemon_command",
    "claim_daemon_command",
    "command_status",
    "daemon_command_snapshot",
    "daemon_command_execution_lock",
    "execute_daemon_command",
    "submit_daemon_command",
]
