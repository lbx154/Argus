"""KernelBench / SOL-ExecBench vertical (Recursive Task 3) — maximize SOL score."""
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
