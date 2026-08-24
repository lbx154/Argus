"""Durable circuit breaker for uncaught mission-runtime failures.

A provider/backend failure is represented by a normal mission outcome and does
not belong here.  This module handles exceptions that escape Argus orchestration
itself: once one such fingerprint is observed, more missions cannot repair the
same loaded code, so Planner/Engineer dispatch is held until the runtime,
relevant source, checkpoint contract, or an explicit canary result changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..core.file_lock import exclusive_file_lock
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..release import release_identity
from .context_packet import CHECKPOINT_CONTRACT_VERSION

CIRCUIT_FILENAME = "runtime-failure-circuit.json"
_CIRCUIT_LOCK_FILENAME = "runtime-failure-circuit.lock"
_SCHEMA_VERSION = 1
_HANDOFF_CHECKPOINT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:|/)[^\n\r'\"]*?[\\/]handoffs[\\/][^\\/\n\r'\"]+"
    r"[\\/]CHECKPOINT\.md"
)
_HANDOFF_ID_RE = re.compile(r"(?i)(handoffs[\\/])[^\\/\s'\"]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / _CIRCUIT_LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        with exclusive_file_lock(
            handle,
            lock_name=f"runtime failure circuit {lock_path}",
        ):
            yield


def _read_unlocked(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _relevant_source_digest() -> str:
    """Cheap hash that changes when the checkpoint settlement code changes."""
    try:
        from ..core import role_session

        path = Path(str(role_session.__file__ or ""))
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        pass
    return ""


def runtime_failure_identity() -> dict[str, Any]:
    """Facts that are allowed to close an existing runtime-failure circuit."""
    try:
        from ..core.runtime_identity import source_root

        identity = release_identity(source_root())
    except Exception:  # noqa: BLE001 - identity failure must fail closed
        identity = {}
    return {
        "release_id": str(identity.get("release_id") or "unknown"),
        "manifest_source_digest": str(identity.get("manifest_source_digest") or ""),
        "runtime_source_digest": str(identity.get("runtime_source_digest") or ""),
        "relevant_source_digest": _relevant_source_digest(),
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
    }


def normalize_runtime_failure_message(message: object) -> str:
    """Remove per-mission/path noise while retaining the causal exception."""
    text = redact_secrets_text(
        str(message or ""),
        known_values=known_secret_values(),
    )
    text = _HANDOFF_CHECKPOINT_RE.sub(
        "<state-root>/handoffs/<mission-id>/CHECKPOINT.md",
        text,
    )
    text = _HANDOFF_ID_RE.sub(r"\1<mission-id>", text)
    return _WHITESPACE_RE.sub(" ", text.replace("\\", "/")).strip()[:2000]


def _exception_callsite(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    selected = frames[-1] if frames else None
    for frame in reversed(frames):
        normalized = frame.filename.replace("\\", "/")
        if "/argus_skill/" in normalized:
            selected = frame
            break
    if selected is None:
        return "unknown"
    filename = selected.filename.replace("\\", "/")
    marker = filename.rfind("/argus_skill/")
    if marker >= 0:
        filename = filename[marker + 1 :]
    return f"{filename}:{selected.name}"


def runtime_failure_fingerprint(exc: BaseException) -> dict[str, str]:
    exception_type = type(exc).__name__
    callsite = _exception_callsite(exc)
    normalized_error = normalize_runtime_failure_message(exc)
    canonical = json.dumps(
        {
            "exception_type": exception_type,
            "callsite": callsite,
            "normalized_error": normalized_error,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
        "exception_type": exception_type,
        "callsite": callsite,
        "normalized_error": normalized_error,
    }


def record_runtime_failure_circuit(
    root: Path | str,
    exc: BaseException,
    *,
    item_id: str = "",
) -> dict[str, Any]:
    """Open or increment the durable circuit for one uncaught exception."""
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    failure = runtime_failure_fingerprint(exc)
    identity = runtime_failure_identity()
    now = time.time()
    with _locked(state_root):
        previous = _read_unlocked(path)
        same = bool(
            previous.get("active")
            and previous.get("fingerprint") == failure["fingerprint"]
            and previous.get("runtime_identity") == identity
        )
        item_ids = [
            str(value)
            for value in (previous.get("item_ids") if same else []) or []
            if str(value)
        ]
        if item_id:
            item_ids.append(str(item_id))
        state = {
            "schema_version": _SCHEMA_VERSION,
            "active": True,
            **failure,
            "runtime_identity": identity,
            "occurrence_count": int(previous.get("occurrence_count") or 0) + 1 if same else 1,
            "first_observed_at": (
                float(previous.get("first_observed_at") or now) if same else now
            ),
            "last_observed_at": now,
            "item_ids": list(dict.fromkeys(item_ids))[-20:],
            "clear_conditions": [
                "release_id_changed",
                "manifest_or_runtime_source_digest_changed",
                "relevant_source_digest_changed",
                "checkpoint_contract_version_changed",
                "reviewed_canary_passed",
            ],
            "cleared_at": 0.0,
            "cleared_reason": "",
            "newly_opened": not same,
        }
        _atomic_json(path, state)
    return state


def active_runtime_failure_circuit(root: Path | str) -> dict[str, Any] | None:
    """Return the active circuit, clearing it after an allowed fact change."""
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    with _locked(state_root):
        state = _read_unlocked(path)
        if not state.get("active"):
            return None
        current_identity = runtime_failure_identity()
        if state.get("runtime_identity") != current_identity:
            state.update(
                active=False,
                cleared_at=time.time(),
                cleared_reason="runtime_identity_changed",
                superseded_runtime_identity=current_identity,
                newly_opened=False,
            )
            _atomic_json(path, state)
            return None
        state.pop("newly_opened", None)
        return state


def clear_runtime_failure_circuit(
    root: Path | str,
    *,
    reason: str,
    fingerprint: str = "",
) -> bool:
    """Close a circuit after a reviewed canary or another explicit fact change."""
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    with _locked(state_root):
        state = _read_unlocked(path)
        if not state.get("active"):
            return False
        if fingerprint and str(state.get("fingerprint") or "") != fingerprint:
            return False
        state.update(
            active=False,
            cleared_at=time.time(),
            cleared_reason=str(reason or "reviewed_canary_passed")[:500],
            newly_opened=False,
        )
        _atomic_json(path, state)
    return True


__all__ = [
    "CIRCUIT_FILENAME",
    "active_runtime_failure_circuit",
    "clear_runtime_failure_circuit",
    "normalize_runtime_failure_message",
    "record_runtime_failure_circuit",
    "runtime_failure_fingerprint",
    "runtime_failure_identity",
]
