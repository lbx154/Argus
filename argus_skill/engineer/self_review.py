"""Engineer-authored self-review and same-session skill-maintenance protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..core.models import ReviewDecision

_DECISION_RE = re.compile(
    r"^ARGUS_ENGINEER_DECISION:\s*(\{[^\n]*\})\s*$",
    flags=re.MULTILINE,
)
_VERIFICATION_RE = re.compile(
    r"^## Verification \(verbatim\)\s*$\s*"
    r"```[^\n]*\n(?P<body>[\s\S]*?)```",
    flags=re.MULTILINE,
)
_REVIEW_VALUES = frozenset({"required", "skip"})
_SKILL_ACTIONS = frozenset({"none", "create", "update"})


@dataclass(frozen=True)
class EngineerCompletionDecision:
    """Machine control authored by the Engineer; prose stays in CHECKPOINT.md."""

    review: str
    # Legacy fields accepted from older agents but no longer requested or
    # persisted by the new protocol.
    reason: str
    verification: str
    skill_action: str = "none"
    skill_name: str = ""
    skill_reason: str = ""

    @property
    def requests_review_skip(self) -> bool:
        return self.review == "skip"


@dataclass(frozen=True)
class EngineerSkillMaintenanceOutcome:
    """Result of the optional same-session skill continuation."""

    attempted: bool = False
    success: bool = False
    summary: str = ""
    thread_id: str | None = None


def parse_engineer_completion_decision(
    message: str,
) -> EngineerCompletionDecision | None:
    """Parse the final one-line Engineer decision; malformed output is ignored."""
    matches = list(_DECISION_RE.finditer(str(message or "")))
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1].group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    review = str(payload.get("review") or "").strip().lower()
    if review not in _REVIEW_VALUES:
        return None
    reason = str(payload.get("reason") or "").strip()[:1000]
    verification = str(payload.get("verification") or "").strip()[:2000]
    skill_action = str(payload.get("skill_action") or "none").strip().lower()
    if skill_action not in _SKILL_ACTIONS:
        skill_action = "none"
    skill_name = str(payload.get("skill_name") or "").strip()[:200]
    skill_reason = str(payload.get("skill_reason") or "").strip()[:1000]
    if skill_action == "update" and not skill_name:
        skill_action = "none"
    return EngineerCompletionDecision(
        review=review,
        reason=reason,
        verification=verification,
        skill_action=skill_action,
        skill_name=skill_name,
        skill_reason=skill_reason,
    )


def verbatim_verification_output(message: str) -> str:
    """Return the last non-empty fenced verification block, if present."""
    blocks = [
        match.group("body").strip()
        for match in _VERIFICATION_RE.finditer(str(message or ""))
        if match.group("body").strip()
    ]
    return blocks[-1] if blocks else ""


def engineer_self_approved_review(
    decision: EngineerCompletionDecision,
    *,
    maintenance_summary: str = "",
) -> ReviewDecision:
    """Synthesize a zero-cost done verdict from an accepted Engineer waiver."""
    _ = maintenance_summary
    return ReviewDecision(
        status="done",
        reason="Engineer used the allowed bounded self-review waiver; see CHECKPOINT.md.",
        next_action="",
        progress_class="decision",
        review_source="engineer_self_review",
        planner_report={
            "forward_progress": True,
            "plan_signal": "continue",
            "evidence_files": [],
        },
    )


__all__ = [
    "EngineerCompletionDecision",
    "EngineerSkillMaintenanceOutcome",
    "engineer_self_approved_review",
    "parse_engineer_completion_decision",
    "verbatim_verification_output",
]
