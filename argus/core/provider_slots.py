"""Portable, host-wide admission for provider processes across all backends."""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import portalocker

from .knobs import resolve_knob

_POLL_SECONDS = 0.25
# Upper bound so a misconfigured wait can never outlive the provider watchdog.
_MAX_WAIT_SECONDS = 600.0


def provider_call_priority(run_label: str) -> int:
    """Interactive work precedes workers; presentation can use idle capacity."""
    if run_label.startswith("map-"):
        return 2
    if run_label.startswith(("manager-frontdoor", "manager-chat", "manager-self")):
        return 0
    return 1


@contextmanager
def _admission_lock(directory: Path):
    # Serializes ticket creation, stale cleanup and slot acquisition across
    # processes. The OS locks on tickets prove liveness, without PID leases.
    with (directory / "admission.lock").open("a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(handle)


def _first_waiter(directory: Path, own: Path) -> Path | None:
    for path in sorted(directory.glob("wait-*.lock")):
        if path == own:
            return path
        try:
            handle = path.open("r+b")
        except FileNotFoundError:
            continue
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException:
            handle.close()
            return path
        else:
            # A dead/timed-out waiter owns no lock. Close before unlinking so
            # Windows can remove the stale ticket as well.
            release_provider_slot(handle)
            path.unlink(missing_ok=True)
    return None


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


def acquire_provider_slot(
    root: Path, *, wait_seconds: float | None = None, priority: int = 1,
    interrupt: Callable[[], str | None] | None = None,
) -> tuple[BinaryIO | None, str]:
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
    reason = interrupt() if interrupt else None
    if reason:
        return None, f"External interrupt: {reason}"
    directory = root / "provider-slots"
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    with _admission_lock(directory):
        ticket = directory / f"wait-{min(2, max(0, priority))}-{time.time_ns():020d}-{uuid.uuid4().hex}.lock"
        waiter = ticket.open("x+b")
        portalocker.lock(waiter, portalocker.LOCK_EX | portalocker.LOCK_NB)
        handle = _try_slots(directory, count) if _first_waiter(directory, ticket) == ticket else None
        if handle is not None:
            release_provider_slot(waiter)
            ticket.unlink(missing_ok=True)
            return handle, ""
    try:
        while True:
            reason = interrupt() if interrupt else None
            if reason:
                return None, f"External interrupt: {reason}"
            with _admission_lock(directory):
                if _first_waiter(directory, ticket) == ticket:
                    handle = _try_slots(directory, count)
                    if handle is not None:
                        release_provider_slot(waiter)
                        ticket.unlink(missing_ok=True)
                        return handle, ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_POLL_SECONDS, remaining))
        return None, f"provider concurrency limit reached ({count} active calls); retry shortly"
    finally:
        with _admission_lock(directory):
            if not waiter.closed:
                release_provider_slot(waiter)
            ticket.unlink(missing_ok=True)


def _try_slots(directory: Path, count: int) -> BinaryIO | None:
    for index in range(count):
        # Descriptors are not inherited by model/tool children. The OS releases
        # a crashed owner's lock; there is no PID lease that can remain stale.
        fd = os.open(directory / f"{index}.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
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
