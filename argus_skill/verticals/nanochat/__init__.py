"""NanoChat Autoresearch vertical (Recursive Task 1) — minimize val_bpb."""
from __future__ import annotations

from .stages import (
    STAGE_ORDER,
    completion_gate,
    role_banner,
)

__all__ = [
    "STAGE_ORDER",
    "completion_gate",
    "role_banner",
]
