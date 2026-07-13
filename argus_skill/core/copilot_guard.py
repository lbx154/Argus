"""Cross-process safety guard for GitHub Copilot-backed Argus calls.

USD mission budgets are not sufficient for Copilot: the provider enforces
premium-request and policy/rate limits, while many Argus control-plane calls
run outside a mission. This module provides one global, persistent guard for
every Copilot call made by every project on the host.
"""
from __future__ import annotations

import json
import logging
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

log = logging.getLogger(__name__)

_STATE_FILE = "copilot-guard.json"
_STATE_LOCK = "copilot-guard.lock"
_USAGE_FILE = "copilot-usage.jsonl"
_SLOT_DIR = "copilot-slots"

_DEFAULT_DAILY_PREMIUM_CAP = 100.0
_DEFAULT_DAILY_CALL_CAP = 300
_DEFAULT_HOURLY_CALL_CAP = 60
_DEFAULT_MAX_CONCURRENCY = 2
_DEFAULT_SLOT_WAIT_SECONDS = 30.0
_DEFAULT_POLICY_COOLDOWN_SECONDS = 24 * 60 * 60
_DEFAULT_RATE_COOLDOWN_SECONDS = 30 * 60

_POLICY_BLOCK_PATTERNS = (
    "access denied by policy settings",
    "subscription does not include this feature",
    "required policies have not been enabled",
    "account suspended",
    "account has been suspended",
)
_RATE_BLOCK_PATTERNS = (
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "quota exceeded",
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _setting(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return raw.strip()
    persisted = persisted_knob(name)
    return persisted.strip() if persisted.strip() else default


def _float_setting(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(_setting(name, str(default))))
    except (TypeError, ValueError):
        return default


def _int_setting(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(_setting(name, str(default))))
    except (TypeError, ValueError):
        return default


def copilot_guard_enabled() -> bool:
    explicit = os.environ.get("ARGUS_SKILL_COPILOT_GUARD")
    if explicit is None and os.environ.get("PYTEST_CURRENT_TEST"):
        # Existing backend unit tests must not mutate the operator's real guard.
        # Guard-specific tests opt in explicitly and point ARGUS_SKILL_HOME at
        # a temporary directory.
        return False
    return _truthy(_setting("ARGUS_SKILL_COPILOT_GUARD", "1"))


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "day": _today(),
        "daily_calls": 0,
        "premium_requests": 0.0,
        "recent_calls": [],
        "blocked_until": 0.0,
        "blocked_reason": "",
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
        blocked_until = float(state.get("blocked_until") or 0.0)
        blocked_reason = str(state.get("blocked_reason") or "")
        state = _default_state()
        state["blocked_until"] = blocked_until
        state["blocked_reason"] = blocked_reason
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
    path = root / _USAGE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return


def _lock_state(root: Path) -> BinaryIO:
    root.mkdir(parents=True, exist_ok=True)
    fh = (root / _STATE_LOCK).open("a+b")
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def _unlock_state(fh: BinaryIO) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    fh.close()


def _acquire_slot(root: Path) -> tuple[BinaryIO | None, str]:
    limit = _int_setting(
        "ARGUS_SKILL_COPILOT_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY
    )
    if limit <= 0 or fcntl is None:
        return None, ""
    wait_s = _float_setting(
        "ARGUS_SKILL_COPILOT_SLOT_WAIT_S", _DEFAULT_SLOT_WAIT_SECONDS
    )
    deadline = time.monotonic() + wait_s
    slot_dir = root / _SLOT_DIR
    slot_dir.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(limit):
            fh = (slot_dir / f"slot-{index}.lock").open("a+b")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            return fh, ""
        if time.monotonic() >= deadline:
            return None, (
                f"global Copilot concurrency cap {limit} reached "
                f"for {wait_s:g}s"
            )
        time.sleep(0.2)


def _release_slot(fh: BinaryIO | None) -> None:
    if fh is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    fh.close()


def _denied_permit(
    *,
    reason: str,
    run_label: str,
    root: Path,
    slot: BinaryIO | None,
    stop_kind: str,
) -> "CopilotPermit":
    """Return a denial without retaining a provider-concurrency slot."""
    _release_slot(slot)
    return CopilotPermit(False, reason, run_label, root, stop_kind=stop_kind)


def _circuit(error_text: str) -> tuple[float, str]:
    low = (error_text or "").casefold()
    if any(pattern in low for pattern in _POLICY_BLOCK_PATTERNS):
        return (
            _float_setting(
                "ARGUS_SKILL_COPILOT_POLICY_COOLDOWN_S",
                _DEFAULT_POLICY_COOLDOWN_SECONDS,
            ),
            "Copilot policy/subscription access denied",
        )
    if any(pattern in low for pattern in _RATE_BLOCK_PATTERNS):
        return (
            _float_setting(
                "ARGUS_SKILL_COPILOT_RATE_COOLDOWN_S",
                _DEFAULT_RATE_COOLDOWN_SECONDS,
            ),
            "Copilot rate/quota limit reached",
        )
    return 0.0, ""


@dataclass
class CopilotPermit:
    allowed: bool
    reason: str
    run_label: str
    root: Path
    stop_kind: str | None = None
    slot: BinaryIO | None = None
    guarded: bool = True
    daily_calls: int = 0
    daily_cap: int = 0
    premium_requests_today: float = 0.0
    premium_cap: float = 0.0
    _finished: bool = False

    def finish(
        self,
        *,
        premium_requests: float = 0.0,
        error_text: str = "",
        success: bool = False,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            if self.allowed and self.guarded:
                lock: BinaryIO | None = None
                try:
                    lock = _lock_state(self.root)
                    state = _load_state(self.root / _STATE_FILE)
                    try:
                        premium = max(0.0, float(premium_requests or 0.0))
                    except (TypeError, ValueError):
                        premium = 0.0
                    state["premium_requests"] = (
                        float(state.get("premium_requests") or 0.0) + premium
                    )
                    cooldown, reason = _circuit(error_text)
                    if cooldown > 0:
                        state["blocked_until"] = max(
                            float(state.get("blocked_until") or 0.0),
                            time.time() + cooldown,
                        )
                        state["blocked_reason"] = reason
                    _write_state(self.root / _STATE_FILE, state)
                    _append_usage(
                        self.root,
                        {
                            "ts": time.time(),
                            "type": "copilot.call.completed",
                            "run_label": self.run_label,
                            "success": bool(success),
                            "premium_requests": premium,
                            "error": (error_text or "")[:500],
                            "blocked_until": state.get("blocked_until", 0.0),
                        },
                    )
                except Exception:  # noqa: BLE001
                    log.warning("Copilot guard accounting failed", exc_info=True)
                finally:
                    if lock is not None:
                        _unlock_state(lock)
        finally:
            _release_slot(self.slot)
            self.slot = None


def acquire_copilot_permit(run_label: str) -> CopilotPermit:
    root = global_root()
    if not copilot_guard_enabled():
        return CopilotPermit(
            True,
            "",
            run_label,
            root,
            guarded=False,
            daily_cap=_int_setting(
                "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
                _DEFAULT_DAILY_CALL_CAP,
            ),
            premium_cap=_float_setting(
                "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
                _DEFAULT_DAILY_PREMIUM_CAP,
            ),
        )

    slot, slot_error = _acquire_slot(root)
    if slot_error:
        return CopilotPermit(
            False,
            slot_error,
            run_label,
            root,
            stop_kind="transient_error",
        )

    lock = _lock_state(root)
    try:
        state = _load_state(root / _STATE_FILE)
        now = time.time()
        blocked_until = float(state.get("blocked_until") or 0.0)
        if blocked_until > now:
            reason = str(state.get("blocked_reason") or "Copilot circuit open")
            return _denied_permit(
                reason=(
                    f"{reason}; retry after "
                    f"{datetime.fromtimestamp(blocked_until).isoformat()}"
                ),
                run_label=run_label,
                root=root,
                slot=slot,
                stop_kind="provider_cooldown",
            )

        premium_cap = _float_setting(
            "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
            _DEFAULT_DAILY_PREMIUM_CAP,
        )
        premium = float(state.get("premium_requests") or 0.0)
        if premium_cap > 0 and premium >= premium_cap:
            return _denied_permit(
                reason=(
                    f"global Copilot daily premium cap {premium_cap:g} reached "
                    f"(used {premium:g})"
                ),
                run_label=run_label,
                root=root,
                slot=slot,
                stop_kind="budget_exhausted",
            )

        daily_cap = _int_setting(
            "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", _DEFAULT_DAILY_CALL_CAP
        )
        daily_calls = int(state.get("daily_calls") or 0)
        if daily_cap > 0 and daily_calls >= daily_cap:
            return _denied_permit(
                reason=f"global Copilot daily call cap {daily_cap} reached",
                run_label=run_label,
                root=root,
                slot=slot,
                stop_kind="budget_exhausted",
            )

        recent = [
            float(value)
            for value in (state.get("recent_calls") or [])
            if isinstance(value, (int, float)) and float(value) >= now - 3600.0
        ]
        hourly_cap = _int_setting(
            "ARGUS_SKILL_COPILOT_HOURLY_CALL_CAP", _DEFAULT_HOURLY_CALL_CAP
        )
        if hourly_cap > 0 and len(recent) >= hourly_cap:
            return _denied_permit(
                reason=f"global Copilot hourly call cap {hourly_cap} reached",
                run_label=run_label,
                root=root,
                slot=slot,
                stop_kind="provider_cooldown",
            )

        recent.append(now)
        state["recent_calls"] = recent[-max(hourly_cap, 1) :]
        state["daily_calls"] = daily_calls + 1
        _write_state(root / _STATE_FILE, state)
        _append_usage(
            root,
            {
                "ts": now,
                "type": "copilot.call.started",
                "run_label": run_label,
                "daily_calls": state["daily_calls"],
                "premium_requests_today": state.get("premium_requests", 0.0),
            },
        )
        return CopilotPermit(
            True,
            "",
            run_label,
            root,
            slot=slot,
            daily_calls=int(state["daily_calls"]),
            daily_cap=daily_cap,
            premium_requests_today=float(state.get("premium_requests") or 0.0),
            premium_cap=premium_cap,
        )
    finally:
        _unlock_state(lock)


def release_denied_permit(permit: CopilotPermit) -> None:
    """Backward-compatible no-op-safe cleanup for callers holding a denial."""
    if permit.allowed:
        return
    permit.finish(error_text=permit.reason)


def trip_copilot_guard(
    reason: str,
    *,
    cooldown_seconds: float = _DEFAULT_POLICY_COOLDOWN_SECONDS,
) -> None:
    """Open the shared circuit without making a provider call."""
    root = global_root()
    lock = _lock_state(root)
    try:
        state = _load_state(root / _STATE_FILE)
        state["blocked_until"] = max(
            float(state.get("blocked_until") or 0.0),
            time.time() + max(0.0, float(cooldown_seconds)),
        )
        state["blocked_reason"] = (reason or "Copilot circuit opened")[:500]
        _write_state(root / _STATE_FILE, state)
    finally:
        _unlock_state(lock)


def copilot_guard_snapshot(*, root: Path | None = None) -> dict[str, Any]:
    root = root or global_root()
    lock = _lock_state(root)
    try:
        state = dict(_load_state(root / _STATE_FILE))
    finally:
        _unlock_state(lock)
    daily_cap = _int_setting(
        "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
        _DEFAULT_DAILY_CALL_CAP,
    )
    premium_cap = _float_setting(
        "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
        _DEFAULT_DAILY_PREMIUM_CAP,
    )
    daily_calls = int(state.get("daily_calls") or 0)
    premium = float(state.get("premium_requests") or 0.0)
    state.update(
        {
            "daily_call_cap": daily_cap,
            "daily_calls_remaining": (
                max(0, daily_cap - daily_calls) if daily_cap > 0 else None
            ),
            "daily_premium_cap": premium_cap,
            "premium_requests_remaining": (
                max(0.0, premium_cap - premium) if premium_cap > 0 else None
            ),
        }
    )
    return state


__all__ = [
    "CopilotPermit",
    "acquire_copilot_permit",
    "copilot_guard_enabled",
    "copilot_guard_snapshot",
    "release_denied_permit",
    "trip_copilot_guard",
]
