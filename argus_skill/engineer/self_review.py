"""Engineer-authored self-review and same-session skill-maintenance protocol."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

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
_WAIT_VALUES = frozenset({"none", "subagent", "external_work"})


@dataclass(frozen=True)
class EngineerCompletionDecision:
    """Machine control authored by the Engineer; prose stays in CHECKPOINT.md."""

    review: str
    skill_action: str = "none"
    skill_name: str = ""
    wait_for: str = "none"
    wait_id: str = ""
    wait_control_present: bool = False

    @property
    def requests_review_skip(self) -> bool:
        return self.review == "skip"

    @property
    def requests_wait(self) -> bool:
        return self.wait_for != "none" and bool(self.wait_id)


@dataclass(frozen=True)
class EngineerSkillMaintenanceOutcome:
    """Result of the optional same-session skill continuation."""

    attempted: bool = False
    success: bool = False
    summary: str = ""
    thread_id: str | None = None


def _parse_engineer_control(payload: object) -> EngineerCompletionDecision | None:
    if not isinstance(payload, dict):
        return None
    review = str(payload.get("review") or "").strip().lower()
    if review not in _REVIEW_VALUES:
        return None
    skill_action = str(payload.get("skill_action") or "none").strip().lower()
    if skill_action not in _SKILL_ACTIONS:
        skill_action = "none"
    skill_name = str(payload.get("skill_name") or "").strip()[:200]
    if skill_action == "update" and not skill_name:
        skill_action = "none"
    wait_for = str(payload.get("wait_for") or "none").strip().lower()
    wait_control_present = "wait_for" in payload or "wait_id" in payload
    if wait_for not in _WAIT_VALUES:
        wait_for = "none"
    wait_id = str(payload.get("wait_id") or "").strip()[:200]
    if wait_for != "none" and not wait_id:
        wait_for = "none"
    if wait_for == "none":
        wait_id = ""
    else:
        # A wait is not a completion decision and cannot maintain a skill.
        review = "required"
        skill_action = "none"
        skill_name = ""
    return EngineerCompletionDecision(
        review=review,
        skill_action=skill_action,
        skill_name=skill_name,
        wait_for=wait_for,
        wait_id=wait_id,
        wait_control_present=wait_control_present,
    )


def engineer_control_path(
    *,
    workdir: Path,
    checkpoint_path: Path | None,
    round_index: int,
    control_scope: str,
) -> Path:
    parent = (
        checkpoint_path.parent
        if checkpoint_path is not None
        else workdir / ".argus" / "runtime" / "engineer-controls"
    )
    scope = hashlib.sha256(
        str(control_scope or workdir.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    root = parent / "engineer-controls" / scope
    return root / f"round-{max(1, int(round_index)):04d}-engineer-control.json"


def prepare_engineer_control(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)


def engineer_control_instructions(
    path: Path,
    *,
    allow_self_review: bool,
    allow_skill_maintenance: bool,
) -> str:
    review_values = "`required` or `skip`" if allow_self_review else "`required`"
    skill_values = (
        "`none`, `create`, or `update`"
        if allow_skill_maintenance
        else "`none`"
    )
    return (
        "## Internal control file\n"
        f"Before your final response, write machine control to `{path}` as JSON. "
        "Do not print it in chat. Use exactly five keys: `review` "
        f"({review_values}), `skill_action` ({skill_values}), and `skill_name` "
        "(required only for update; otherwise empty), plus `wait_for` (`none`, "
        "`subagent`, or `external_work`) and `wait_id` (the exact registry id, "
        "otherwise empty). A wait forces `review=required` and "
        "`skill_action=none`. Your final response remains ordinary prose."
    )


def read_engineer_completion_decision(
    path: Path,
) -> EngineerCompletionDecision | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return _parse_engineer_control(payload)


def parse_engineer_completion_decision(
    message: str,
) -> EngineerCompletionDecision | None:
    """Legacy marker adapter for already-running older agents."""
    matches = list(_DECISION_RE.finditer(str(message or "")))
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1].group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return _parse_engineer_control(payload)


def strip_legacy_engineer_control(message: str) -> str:
    """Remove legacy marker lines before prose reaches Reviewer/events."""
    return _DECISION_RE.sub("", str(message or "")).rstrip()


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
    "engineer_control_instructions",
    "engineer_control_path",
    "engineer_self_approved_review",
    "parse_engineer_completion_decision",
    "prepare_engineer_control",
    "read_engineer_completion_decision",
    "strip_legacy_engineer_control",
    "verbatim_verification_output",
]
