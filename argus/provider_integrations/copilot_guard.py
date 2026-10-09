"""Cross-process circuit for GitHub Copilot-backed Argus calls.

Copilot enforces policy and rate limits of its own, and many Argus
control-plane calls run outside a mission. This module provides one global,
persistent circuit for every Copilot call made by every project on the host:
a concurrency ceiling, plus a cooldown after a policy or rate refusal so the
next call does not repeat a request the provider has just refused.

Spending is not this module's concern. Every call is recorded in the usage
ledger for display, and a hosted trial is limited at its gateway.
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

import portalocker

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX production path
    fcntl = None  # type: ignore[assignment]

from ..core.http_status import has_http_status
from ..core.knob_store import persisted_knob
from ..core.paths import global_root
from ..core.runner_errors import is_execution_host_startup_error
from ..core.runner_receipts import is_provider_turn_cap_receipt

log = logging.getLogger(__name__)

_STATE_FILE = "copilot-guard.json"
_STATE_LOCK = "copilot-guard.lock"
_SLOT_DIR = "copilot-slots"

_DEFAULT_MAX_CONCURRENCY = 10_000
_DEFAULT_SLOT_WAIT_SECONDS = 0.0
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


def _default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "blocked_until": 0.0,
        "blocked_reason": "",
        "updated_at": time.time(),
    }


def _load_state(path: Path) -> dict[str, Any]:
    """Read the circuit; an older file's call counters are simply ignored."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(value, dict):
        return _default_state()
    state = _default_state()
    try:
        state["blocked_until"] = float(value.get("blocked_until") or 0.0)
    except (TypeError, ValueError):
        state["blocked_until"] = 0.0
    state["blocked_reason"] = str(value.get("blocked_reason") or "")
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


def _lock_state(root: Path) -> BinaryIO:
    root.mkdir(parents=True, exist_ok=True)
    fh = (root / _STATE_LOCK).open("a+b")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        else:
            portalocker.lock(fh, portalocker.LOCK_EX)
    except (OSError, portalocker.exceptions.LockException):
        fh.close()
        raise
    return fh


def _unlock_state(fh: BinaryIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        else:
            portalocker.unlock(fh)
    except (OSError, portalocker.exceptions.LockException):
        pass
    finally:
        fh.close()


def _acquire_slot(root: Path) -> tuple[BinaryIO | None, str]:
    limit = _int_setting(
        "ARGUS_SKILL_COPILOT_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY
    )
    if limit <= 0:
        return None, ""
    wait_s = _float_setting(
        "ARGUS_SKILL_COPILOT_SLOT_WAIT_S", _DEFAULT_SLOT_WAIT_SECONDS
    )
    deadline = time.monotonic() + wait_s if wait_s > 0 else None
    slot_dir = root / _SLOT_DIR
    slot_dir.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(limit):
            fh = (slot_dir / f"slot-{index}.lock").open("a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    portalocker.lock(
                        fh,
                        portalocker.LOCK_EX | portalocker.LOCK_NB,
                    )
            except (OSError, portalocker.exceptions.LockException):
                fh.close()
                continue
            return fh, ""
        if deadline is not None and time.monotonic() >= deadline:
            return None, (
                f"global Copilot concurrency cap {limit} reached "
                f"for {wait_s:g}s"
            )
        time.sleep(0.2)


def _release_slot(fh: BinaryIO | None) -> None:
    if fh is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        else:
            portalocker.unlock(fh)
    except (OSError, portalocker.exceptions.LockException):
        pass
    finally:
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
    if is_provider_turn_cap_receipt(error_text) or is_execution_host_startup_error(error_text):
        # A local terminal stop can retain earlier recovered provider errors.
        # That history is not a reason to open the circuit.
        return 0.0, ""
    low = (error_text or "").casefold()
    if any(pattern in low for pattern in _POLICY_BLOCK_PATTERNS):
        return (
            _float_setting(
                "ARGUS_SKILL_COPILOT_POLICY_COOLDOWN_S",
                _DEFAULT_POLICY_COOLDOWN_SECONDS,
            ),
            "Copilot policy/subscription access denied",
        )
    if has_http_status(low, {429}) or any(pattern in low for pattern in _RATE_BLOCK_PATTERNS):
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
    _finished: bool = False

    def finish(
        self,
        *,
        error_text: str = "",
        success: bool = False,
    ) -> None:
        """Release the concurrency slot; open the circuit after a provider refusal."""
        if self._finished:
            return
        self._finished = True
        try:
            if self.allowed and self.guarded and not success:
                cooldown, reason = _circuit(error_text)
                if cooldown > 0:
                    lock: BinaryIO | None = None
                    try:
                        lock = _lock_state(self.root)
                        state = _load_state(self.root / _STATE_FILE)
                        state["blocked_until"] = max(
                            float(state.get("blocked_until") or 0.0),
                            time.time() + cooldown,
                        )
                        state["blocked_reason"] = reason
                        _write_state(self.root / _STATE_FILE, state)
                    except Exception:  # noqa: BLE001
                        log.warning("Copilot circuit state could not be written", exc_info=True)
                    finally:
                        if lock is not None:
                            _unlock_state(lock)
        finally:
            _release_slot(self.slot)
            self.slot = None


def acquire_copilot_permit(run_label: str) -> CopilotPermit:
    root = global_root()
    if not copilot_guard_enabled():
        return CopilotPermit(True, "", run_label, root, guarded=False)

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
        blocked_until = float(state.get("blocked_until") or 0.0)
        if blocked_until > time.time():
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
        return CopilotPermit(True, "", run_label, root, slot=slot)
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
    """The circuit as persisted: when it is open and why."""
    root = root or global_root()
    lock = _lock_state(root)
    try:
        return dict(_load_state(root / _STATE_FILE))
    finally:
        _unlock_state(lock)


__all__ = [
    "CopilotPermit",
    "acquire_copilot_permit",
    "copilot_guard_enabled",
    "copilot_guard_snapshot",
    "release_denied_permit",
    "trip_copilot_guard",
]
