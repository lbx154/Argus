"""Operator inbox intake: stop before acceptance when the round is interrupted."""
from __future__ import annotations

from ..core.file_lock import FileLockCancelled
from ..core.run_gateway import current_run_interrupt_reason


def check_intake_cancelled() -> None:
    if current_run_interrupt_reason():
        raise FileLockCancelled("operator intake cancelled before acceptance")

