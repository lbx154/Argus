"""Cross-process admission slots for tool-using role calls."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - production evaluation is POSIX
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def role_call_slot(role: str) -> Iterator[None]:
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in str(role or "").strip().lower()
    ).strip("_")
    if not normalized or fcntl is None:
        yield
        return
    env_name = f"ARGUS_SKILL_{normalized.upper()}_MAX_CONCURRENCY"
    try:
        slots = int(os.environ.get(env_name, "0"))
    except ValueError:
        slots = 0
    if slots <= 0:
        yield
        return
    lock_root = Path(
        os.environ.get(
            "ARGUS_SKILL_ROLE_SLOT_ROOT",
            "/tmp/argus-skill-role-slots",
        )
    ) / normalized
    lock_root.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(slots):
            handle = (lock_root / f"slot-{index:02d}.lock").open("a+")
            try:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            return
        time.sleep(0.1)
