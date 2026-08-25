"""Workflow for changing Argus itself."""
from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["inspect", "change", "verify"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)
WORKFLOW_MODE = "staged"
VERIFICATION_STAGE_PROFILES = {
    "inspect": "explore",
    "change": "develop",
    "verify": "certify",
}
MISSION_KIND = "software"
GROUND_BEFORE_HANDOFF = True
REQUIRE_INDEPENDENT_REVIEW = True
completion_gate = "none"

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "inspect": (
        ChecklistItem(
            id="inspect.current_behavior",
            statement=(
                "Read the real call path and closest reusable implementation. Run the "
                "maintenance audit and decide which relevant findings are actual problems."
            ),
            evidence_hint="source locations, callers, and audit output",
        ),
    ),
    "change": (
        ChecklistItem(
            id="change.small_reusable_patch",
            statement=(
                "Make the coherent change justified by the causal surface. Keep generic orchestration in core "
                "and concrete tools, Skills, stages, and workflow in their vertical."
            ),
            evidence_hint="the source diff",
        ),
    ),
    "verify": (
        ChecklistItem(
            id="verify.real_behavior",
            statement=(
                "Run the focused regression and affected test/build commands. Confirm the "
                "requested behavior rather than a count of static findings."
            ),
            evidence_hint="exact commands and results",
        ),
    ),
}


def role_banner(role: str) -> str:
    common = (
        "ARGUS MAINTENANCE: improve Argus itself with coherent, reusable changes. "
        "Core owns generic orchestration and long-running state; verticals own "
        "domain tools, Skills, stages, checklists, and workflow. Audit matches are "
        "candidates, not automatic edits. Remove code that has no behavior or caller."
    )
    if role == "reviewer":
        return (
            common
            + " Review the changed causal surface and reuse unchanged evidence; "
            "certify the full result only at the verify stage."
        )
    return common


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "VERIFICATION_STAGE_PROFILES",
    "completion_gate",
    "role_banner",
]
