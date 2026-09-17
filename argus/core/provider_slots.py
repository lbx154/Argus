"""Portable, host-wide admission for provider processes across all backends."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import BinaryIO

import portalocker

from .knobs import resolve_knob

_POLL_SECONDS = 0.25
# Upper bound so a misconfigured wait can never outlive the provider watchdog.
_MAX_WAIT_SECONDS = 600.0


def provider_slot_wait_seconds() -> float:
    """How long a caller queues for a busy slot before it is turned away.

    A pool of route workers can legitimately hold every slot for minutes; the
    lead's Planner or Manager call then used to fail instantly and put the
    whole mission into an ever-longer provider cooldown. Queueing briefly lets
    the call go through as soon as a worker's call returns.
    """
    raw = resolve_knob("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "45").value
    try:
        seconds = float(str(raw).strip() or 0)
    except ValueError as exc:
        raise ValueError("provider slot wait must be a non-negative number of seconds") from exc
    if seconds < 0:
        raise ValueError("provider slot wait must be a non-negative number of seconds")
    return min(seconds, _MAX_WAIT_SECONDS)


def acquire_provider_slot(root: Path, *, wait_seconds: float | None = None) -> tuple[BinaryIO | None, str]:
    # Explicitly opt in. Older installations keep their provider-specific
    # limits; the public trial sets this to its shared process allowance.
    raw = resolve_knob("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "0").value
    try:
        count = int(raw)
    except ValueError as exc:
        raise ValueError("provider concurrency must be a non-negative integer") from exc
    if count < 0 or count > 1024:
        raise ValueError("provider concurrency must be between 0 and 1024")
    if count == 0:
        return None, ""
    if wait_seconds is None:
        wait_seconds = provider_slot_wait_seconds()
    directory = root / "provider-slots"
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        handle = _try_slots(directory, count)
        if handle is not None:
            return handle, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_POLL_SECONDS, remaining))
    return None, f"provider concurrency limit reached ({count} active calls); retry shortly"


def _try_slots(directory: Path, count: int) -> BinaryIO | None:
    for index in range(count):
        # Descriptors are not inherited by model/tool children. The OS releases
        # a crashed owner's lock; there is no PID lease that can remain stale.
        fd = os.open(directory / f"{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        handle = os.fdopen(fd, "a+b")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            handle.close()
            continue
        except BaseException:
            handle.close()
            raise
        return handle
    return None


def release_provider_slot(handle: BinaryIO | None) -> None:
    if handle is not None:
        try:
            portalocker.unlock(handle)
        finally:
            handle.close()
