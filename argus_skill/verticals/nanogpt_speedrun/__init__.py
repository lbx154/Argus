"""NanoGPT Speedrun vertical (Recursive Task 2) — minimize time-to-val_loss 3.28."""
from __future__ import annotations

from .stages import (
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
    completion_gate,
    role_banner,
)

__all__ = [
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
]
