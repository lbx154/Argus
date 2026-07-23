"""Lean one-mission workflow for bounded standalone deliverables."""

from __future__ import annotations

STAGE_ORDER = ["delivery"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)
CHECKLIST_OPTIONAL_STAGES = ("delivery",)
CHECKLIST_ITEMS: dict[str, tuple[object, ...]] = {"delivery": ()}
completion_gate = "none"
WORKFLOW_MODE = "direct"


def role_banner(role: str) -> str:
    return (
        "DIRECT WORKFLOW: satisfy the operator's actual request without inventing "
        "research stages, mandatory scaffolding, or extra acceptance gates. "
        "Produce or review the deliverable proportionally."
    )
