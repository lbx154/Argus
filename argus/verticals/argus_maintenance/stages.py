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
# Round guards (core/round_policy.py). Changing Argus itself is system
# verification: the verify stage runs the full test suite, CI, and live
# checks, and a failing check followed by several repair rounds is the normal
# path, not a stall. The round-count guards are off; the Reviewer's explicit
# FORWARD_PROGRESS=false streak decides, with room for a long repair. A
# Reviewer that stops giving any progress judgement is caught after 200 rounds.
ROUND_POLICY = {
    "stall_threshold": 8,
    "no_progress_threshold": 2,
    "soft_round_limit": 0,
    "hard_escalate_rounds": 200,
}

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "inspect": (
        ChecklistItem(
            id="inspect.current_behavior",
            statement=(
                "Read the real call path and the closest reusable implementation. Run "
                "the repository architecture scan and decide which of the relevant "
                "findings are actual problems."
            ),
            evidence_hint="source locations, callers, and the architecture scan output",
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
        "domain tools, Skills, stages, the questions each stage asks, and workflow. "
        "Architecture-scan matches are candidates, not automatic edits. Remove code "
        "that has no behavior or caller."
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
