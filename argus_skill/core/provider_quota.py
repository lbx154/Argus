"""Persistent daily provider-request accounting shared across all projects."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX production path
    fcntl = None  # type: ignore[assignment]

from .knob_store import persisted_knob
from .paths import global_root

_CODEX_STATE_FILE = "codex-quota.json"
_CODEX_LOCK_FILE = "codex-quota.lock"
_CODEX_USAGE_FILE = "codex-usage.jsonl"
_DEFAULT_CODEX_DAILY_CALL_CAP = 300


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _int_setting(name: str, default: int) -> int:
    raw = os.environ.get(name) or persisted_knob(name) or str(default)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "day": _today(),
        "daily_calls": 0,
        "completed_calls": 0,
        "failed_calls": 0,
        "updated_at": time.time(),
    }


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(value, dict):
        return _default_state()
    state = _default_state()
    state.update(value)
    if str(state.get("day") or "") != _today():
        state = _default_state()
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.time()
    tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _append_usage(root: Path, row: dict[str, Any]) -> None:
    try:
        path = root / _CODEX_USAGE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _lock(root: Path) -> BinaryIO:
    root.mkdir(parents=True, exist_ok=True)
    fh = (root / _CODEX_LOCK_FILE).open("a+b")
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def _unlock(fh: BinaryIO) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    fh.close()


def _guard_enabled() -> bool:
    explicit = str(os.environ.get("ARGUS_SKILL_CODEX_GUARD", "") or "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    # Existing backend tests should not mutate the operator's real accounting.
    # Quota-specific tests opt in explicitly with an isolated HOME.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def codex_quota_enabled() -> bool:
    return _guard_enabled()


@dataclass
class ProviderPermit:
    allowed: bool
    reason: str
    provider: str
    run_label: str
    root: Path
    daily_calls: int = 0
    daily_cap: int = 0
    guarded: bool = True
    _finished: bool = False

    def finish(self, *, success: bool, error_text: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        if not self.allowed or not self.guarded:
            return
        lock = _lock(self.root)
        try:
            state = _load_state(self.root / _CODEX_STATE_FILE)
            key = "completed_calls" if success else "failed_calls"
            state[key] = int(state.get(key) or 0) + 1
            _write_state(self.root / _CODEX_STATE_FILE, state)
            _append_usage(
                self.root,
                {
                    "ts": time.time(),
                    "type": "provider.request.completed",
                    "provider": self.provider,
                    "run_label": self.run_label,
                    "success": bool(success),
                    "error": (error_text or "")[:500],
                    "daily_calls": state.get("daily_calls", 0),
                    "daily_cap": self.daily_cap,
                },
            )
        finally:
            _unlock(lock)


def acquire_codex_permit(run_label: str) -> ProviderPermit:
    root = global_root()
    cap = _int_setting(
        "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
        _DEFAULT_CODEX_DAILY_CALL_CAP,
    )
    if not _guard_enabled():
        return ProviderPermit(True, "", "codex", run_label, root, daily_cap=cap, guarded=False)

    lock = _lock(root)
    try:
        state = _load_state(root / _CODEX_STATE_FILE)
        used = int(state.get("daily_calls") or 0)
        if cap > 0 and used >= cap:
            reason = f"global Codex daily call cap {cap} reached (used {used})"
            _append_usage(
                root,
                {
                    "ts": time.time(),
                    "type": "provider.request.denied",
                    "provider": "codex",
                    "run_label": run_label,
                    "reason": reason,
                    "daily_calls": used,
                    "daily_cap": cap,
                },
            )
            return ProviderPermit(False, reason, "codex", run_label, root, used, cap)

        used += 1
        state["daily_calls"] = used
        _write_state(root / _CODEX_STATE_FILE, state)
        _append_usage(
            root,
            {
                "ts": time.time(),
                "type": "provider.request.started",
                "provider": "codex",
                "run_label": run_label,
                "daily_calls": used,
                "daily_cap": cap,
            },
        )
        return ProviderPermit(True, "", "codex", run_label, root, used, cap)
    finally:
        _unlock(lock)


def codex_quota_snapshot(*, root: Path | None = None) -> dict[str, Any]:
    root = root or global_root()
    cap = _int_setting(
        "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
        _DEFAULT_CODEX_DAILY_CALL_CAP,
    )
    lock = _lock(root)
    try:
        state = dict(_load_state(root / _CODEX_STATE_FILE))
    finally:
        _unlock(lock)
    used = int(state.get("daily_calls") or 0)
    return {
        "provider": "codex",
        "day": state.get("day", _today()),
        "daily_calls": used,
        "daily_cap": cap,
        "remaining": max(0, cap - used) if cap > 0 else None,
        "completed_calls": int(state.get("completed_calls") or 0),
        "failed_calls": int(state.get("failed_calls") or 0),
    }


def provider_usage_snapshot(*, root: Path | None = None) -> dict[str, Any]:
    from .copilot_guard import copilot_guard_snapshot

    resolved_root = root or global_root()
    copilot = copilot_guard_snapshot(root=resolved_root)
    return {
        "day": _today(),
        "codex": codex_quota_snapshot(root=resolved_root),
        "copilot": {
            "provider": "copilot",
            "day": copilot.get("day", _today()),
            "daily_calls": int(copilot.get("daily_calls") or 0),
            "daily_cap": int(copilot.get("daily_call_cap") or 0),
            "remaining": copilot.get("daily_calls_remaining"),
            "premium_requests": float(copilot.get("premium_requests") or 0.0),
            "premium_cap": float(copilot.get("daily_premium_cap") or 0.0),
            "premium_remaining": copilot.get("premium_requests_remaining"),
            "blocked_until": float(copilot.get("blocked_until") or 0.0),
            "blocked_reason": str(copilot.get("blocked_reason") or ""),
        },
    }


__all__ = [
    "ProviderPermit",
    "acquire_codex_permit",
    "codex_quota_enabled",
    "codex_quota_snapshot",
    "provider_usage_snapshot",
]
