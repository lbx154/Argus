"""Explicit shared contract surface for four-stage metric optimization."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPTIMIZATION_STAGE_ORDER = ("setup", "optimize", "measure", "report")


@dataclass(frozen=True)
class OptimizationBaseContract:
    stage_order: tuple[str, ...]
    checklist_items: dict[str, Any]


def speedrun_base_contract() -> OptimizationBaseContract:
    """Return independent containers for a speedrun-compatible specialization."""
    from .speedrun import stages

    return OptimizationBaseContract(
        stage_order=OPTIMIZATION_STAGE_ORDER,
        checklist_items=dict(stages.CHECKLIST_ITEMS),
    )


__all__ = [
    "OPTIMIZATION_STAGE_ORDER",
    "OptimizationBaseContract",
    "speedrun_base_contract",
]
