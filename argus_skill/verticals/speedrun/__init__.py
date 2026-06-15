"""Speedrun vertical — quantitative-optimization missions on top of argus core.

Single-target, single-metric missions with a wall-clock budget: GPU
kernel speedrun, training-script optimization (nanochat_autoresearch,
NanoGPT speedrun), latency optimization, etc.

Four stages: ``setup → optimize → measure → report``. See
``argus_skill.verticals.speedrun.stages`` for the per-stage shell
checks and reviewer checklists.

No paper artifacts, no LaTeX, no figures. The verdict is mechanical:
mean metric over N seeds vs reference baseline.
"""
from __future__ import annotations

from .stages import (
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
)

__all__ = [
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
]
