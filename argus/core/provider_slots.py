"""Portable, host-wide admission for provider processes across all backends."""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

import portalocker

from .knobs import resolve_knob


def acquire_provider_slot(root: Path) -> tuple[BinaryIO | None, str]:
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
    directory = root / "provider-slots"
    directory.mkdir(parents=True, exist_ok=True)
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
        return handle, ""
    return None, f"provider concurrency limit reached ({count} active calls); retry shortly"


def release_provider_slot(handle: BinaryIO | None) -> None:
    if handle is not None:
        try:
            portalocker.unlock(handle)
        finally:
            handle.close()
