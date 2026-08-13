"""Deterministic preflight shared by direct and supervised experiment jobs."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import portalocker

from ._registry import REGISTRY_DIR, _is_pid_alive, _list_tasks

_STALE_RUNNING_SECONDS = 15 * 60.0
_LOCAL_INPUT_FLAGS = frozenset({
    "config",
    "curriculum",
    "data",
    "data_file",
    "dataset_file",
    "input",
    "manifest",
    "matrix",
    "tasks",
})
_PATH_LIKE_INPUT_FLAGS = frozenset({
    "curriculum",
    "data_file",
    "dataset_file",
    "manifest",
    "matrix",
    "tasks",
})
_SHELL_BUILTINS = frozenset({
    "cd",
    "command",
    "exec",
    "export",
    "if",
    "set",
    "source",
    "test",
})
_CLAIMS_LOCK = threading.Lock()
_HELD_CLAIMS: dict[tuple[str, str], Any] = {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _flags(command: str) -> dict[str, str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {}
    result: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            key, separator, value = token[2:].partition("=")
            if not separator and index + 1 < len(tokens):
                candidate = tokens[index + 1]
                if not candidate.startswith("--"):
                    value = candidate
                    index += 1
            result[key.replace("-", "_")] = value
        index += 1
    return result


def _command_executable(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    index = 0
    if tokens and tokens[0] == "env":
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                index += 1
                break
            if token in {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"}:
                index += 1
                continue
            if token in {"-u", "--unset", "-C", "--chdir"}:
                index += 2
                continue
            if token.startswith(("--unset=", "--chdir=")):
                index += 1
                continue
            if token in {"-S", "--split-string"} or token.startswith("--split-string="):
                return ""
            break
    while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
        name, _, _value = tokens[index].partition("=")
        if not name.replace("_", "").isalnum():
            break
        index += 1
    return tokens[index] if index < len(tokens) else ""


def _looks_like_local_path(name: str, value: str) -> bool:
    if not value or urlparse(value).scheme in {"http", "https", "s3", "gs"}:
        return False
    return (
        value.startswith((".", "/", "~"))
        or (name in _PATH_LIKE_INPUT_FLAGS and "/" in value)
        or Path(value).suffix.lower()
        in {".csv", ".json", ".jsonl", ".parquet", ".tsv", ".yaml", ".yml"}
    )


def _can_resolve_inputs_against_cwd(command: str) -> bool:
    """Only validate paths when the shell does not change their base directory."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return "cd" not in tokens and not any(
        token in {"&&", "||", ";", "|", "(", ")"}
        for token in tokens
    )


def _task_run_dir(task: dict[str, Any]) -> Path | None:
    raw = str(task.get("run_dir") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        cwd = Path(str(task.get("cwd") or ".")).expanduser()
        path = cwd / path
    return path.resolve()


def _live_run_owner(run_dir: Path, *, task_id: str) -> dict[str, Any] | None:
    for task in _list_tasks():
        if str(task.get("task_id") or "") == task_id:
            continue
        if str(task.get("state") or "") not in {"preflight", "running", "starting"}:
            continue
        owner_dir = _task_run_dir(task)
        if owner_dir != run_dir:
            continue
        try:
            pid = int(task.get("pid") or task.get("worker_pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and _is_pid_alive(pid):
            return task
    return None


def _claim_path(run_dir: Path) -> Path:
    digest = hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()
    return REGISTRY_DIR / "run_claims" / f"{digest}.lock"


def _claim_run_dir(
    run_dir: Path,
    *,
    task_id: str,
    claim_owner: str,
) -> str:
    claim_path = _claim_path(run_dir)
    key = (claim_owner, str(run_dir))
    with _CLAIMS_LOCK:
        for (owner, claimed_dir), _handle in _HELD_CLAIMS.items():
            if claimed_dir == str(run_dir):
                return f"experiment run directory is already claimed by task {owner}: {run_dir}"
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        handle = claim_path.open("a+", encoding="utf-8")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            try:
                handle.seek(0)
                owner = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError):
                owner = {}
            handle.close()
            return (
                "experiment run directory is already claimed"
                + (
                    f" by task {owner.get('task_id')}"
                    if owner.get("task_id")
                    else ""
                )
                + f": {run_dir}"
            )
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "task_id": task_id,
                "claim_owner": claim_owner,
                "worker_pid": os.getpid(),
                "run_dir": str(run_dir),
                "claimed_at": time.time(),
            },
            handle,
            ensure_ascii=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        _HELD_CLAIMS[key] = handle
    return ""


def release_experiment_launch_claim(
    *,
    task_id: str,
    cwd: str,
    run_dir: str | None,
    claim_owner: str | None = None,
) -> None:
    if not run_dir:
        return
    path = Path(run_dir).expanduser()
    path = path if path.is_absolute() else Path(cwd).expanduser() / path
    key = (str(claim_owner or task_id), str(path.resolve()))
    with _CLAIMS_LOCK:
        handle = _HELD_CLAIMS.pop(key, None)
    if handle is None:
        return
    try:
        portalocker.unlock(handle)
    finally:
        handle.close()


def _reconcile_run_status(
    run_dir: Path,
    *,
    task_id: str,
    now: float,
    stale_after_seconds: float,
) -> str:
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return ""
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"experiment status is unreadable: {status_path}: {exc}"
    if not isinstance(status, dict):
        return f"experiment status is malformed: {status_path}"
    if str(status.get("state") or "").lower() != "running":
        return ""
    owner = _live_run_owner(run_dir, task_id=task_id)
    if owner is not None:
        return (
            "experiment run directory is already owned by live task "
            f"{owner.get('task_id')}: {run_dir}"
        )
    try:
        updated_at = float(status.get("updated_at") or status_path.stat().st_mtime)
    except (OSError, TypeError, ValueError):
        updated_at = now
    age = max(0.0, now - updated_at)
    if age < stale_after_seconds:
        return (
            "experiment status says running without a registered live owner; "
            f"wait or reconcile after {stale_after_seconds:g}s: {status_path}"
        )
    status.update({
        "state": "failed",
        "error": "stale running status reconciled before relaunch",
        "reconciled_at": now,
        "stale_age_seconds": age,
    })
    _atomic_json(status_path, status)
    return ""


def experiment_launch_preflight(
    *,
    task_id: str,
    command: str,
    cwd: str,
    run_dir: str | None,
    claim_owner: str | None = None,
    now: float | None = None,
    stale_after_seconds: float = _STALE_RUNNING_SECONDS,
) -> tuple[bool, str]:
    """Reject deterministic zero-work launches before a process is spawned."""
    base = Path(cwd).expanduser().resolve()
    if not base.is_dir():
        return True, f"working directory does not exist: {base}"
    if not command.strip():
        return True, "launch command is empty"

    executable = _command_executable(command)
    if executable and executable not in _SHELL_BUILTINS:
        candidate = Path(executable).expanduser()
        if "/" in executable:
            candidate = candidate if candidate.is_absolute() else base / candidate
            if not candidate.exists():
                return True, f"launch executable does not exist: {candidate}"
        elif shutil.which(executable) is None:
            available = subprocess.run(
                ["bash", "-lc", f"command -v -- {shlex.quote(executable)}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).returncode == 0
            if not available:
                return True, f"launch executable is not available on PATH: {executable}"

    if _can_resolve_inputs_against_cwd(command):
        for name, value in _flags(command).items():
            if name not in _LOCAL_INPUT_FLAGS or not _looks_like_local_path(name, value):
                continue
            path = Path(value).expanduser()
            path = path if path.is_absolute() else base / path
            if not path.exists():
                return True, (
                    f"required --{name.replace('_', '-')} input does not exist: {path}"
                )

    if run_dir:
        resolved = Path(run_dir).expanduser()
        resolved = resolved if resolved.is_absolute() else base / resolved
        resolved = resolved.resolve()
        if (resolved / "STOP").exists():
            return True, f"experiment STOP file is present: {resolved / 'STOP'}"
        if resolved.exists():
            issue = _reconcile_run_status(
                resolved,
                task_id=task_id,
                now=float(now if now is not None else time.time()),
                stale_after_seconds=max(1.0, float(stale_after_seconds)),
            )
            if issue:
                return True, issue
        claim_issue = _claim_run_dir(
            resolved,
            task_id=task_id,
            claim_owner=str(claim_owner or task_id),
        )
        if claim_issue:
            return True, claim_issue
    return False, ""


__all__ = [
    "experiment_launch_preflight",
    "release_experiment_launch_claim",
]
