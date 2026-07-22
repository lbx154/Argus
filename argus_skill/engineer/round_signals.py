"""Cross-round-phase review signal helpers for the Engineer round loop.

These small pure functions turn a single ``ReviewDecision`` into the derived
signals the round loop needs across multiple phases: the event payload sent
to ``on_event``, the Dynamic-Plan ``plan_signal`` event, whether a Reviewer
``continue`` verdict actually asks for Manager/Planner scope arbitration,
whether it names an earlier broken pipeline stage that needs Manager-owned
rollback, the coarse "did this round make decision progress" classification
used for the semantic-stall streak, and the background/external-work cadence
wait helpers shared by the mid-round wait check and the round-settlement
``wait_for_subagent`` control action. Like ``round_stop_signals``, this is a
leaf module with no dependency on ``runner.py``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType
from ..core.models import ReviewDecision
from ..core.secret_guard import (
    SecretScrubReport,
    known_secret_values,
    redact_secrets_record,
    scrub_recent_text_artifacts,
)
from .background_subagents import inspect_wait_target, wait_for_subagent_cadence
from .external_work import wait_for_external_work_cadence

_REVIEW_SCOPE_CHANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:authorize|create|enqueue|insert|schedule|open|start)\b"
        r".{0,100}\b(?:new|separate|scoped|replacement|repair)?\s*"
        r"(?:mission|task|plan)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:new|separate|replacement|scoped)\s+(?:repair\s+)?"
        r"(?:mission|task|plan)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\breturn\s+control\s+to\s+(?:the\s+)?(?:planner|manager)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:change|relax|replace|rewrite|expand|broaden|modify)\b"
        r".{0,80}\b(?:scope|objective|acceptance|non[- ]?goals?|budget|"
        r"resources?|stage)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:新建|创建|插入|安排|授权|重新规划).{0,40}"
        r"(?:任务|mission|计划|修复任务)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:扩大|放宽|修改|替换|变更).{0,40}"
        r"(?:范围|目标|验收|非目标|预算|资源|阶段)",
        re.IGNORECASE | re.DOTALL,
    ),
)

_EARLIEST_BROKEN_STAGE_PATTERNS = (
    re.compile(
        r"(?i)\bearliest[_\s-]*broken[_\s-]*stage\b\s*(?:is|[:=])\s*`?([a-z0-9_-]+)"
    ),
    re.compile(
        r"(?i)\bearliest\s+broken\s+stage\s+is\s+`?([a-z0-9_-]+)"
    ),
    re.compile(
        r"(?i)\b(?:rollback|return|reopen)\b"
        r"(?:\s+[a-z0-9_-]+){0,6}\s+(?:to\s+)?`?"
        r"(research|plan|benchmark|run|analysis|draft|review|submission)\b"
    ),
)

_DECISION_PROGRESS_CLASSES = frozenset({"decision", "evidence"})
_NONDECISION_PROGRESS_CLASSES = frozenset({
    "setup_only",
    "artifact_sync_only",
    "none",
})


def _review_event_payload(
    review: ReviewDecision,
    *,
    round_index: int,
    round_max: int,
    text: str,
    review_skipped: bool = False,
    review_source: str = "",
) -> dict[str, object]:
    """Adapter — runner adds ``round_max`` / ``text`` / ``review_skipped``
    on top of the canonical reviewer payload. The reviewer JSON schema's
    full field set lives in ``ReviewDecision.to_event_payload``; this
    keeps engineer-runner and mission-engine emit sites consistent."""
    return redact_secrets_record(
        review.to_event_payload(
            round_index=round_index,
            round_max=round_max,
            text=text,
            review_skipped=review_skipped,
            review_source=review_source,
        ),
        known_values=known_secret_values(),
    )


def _normalize_dynamic_plan_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "off"


def _plan_signal_event(
    review: ReviewDecision,
    *,
    mode: str = "shadow",
    streak: int = 1,
    confirm_rounds: int = 2,
) -> dict[str, object] | None:
    report = getattr(review, "planner_report", None)
    mode = _normalize_dynamic_plan_mode(mode)
    if (
        mode == "off"
        or not isinstance(report, dict)
        or report.get("plan_signal") != "reconsider"
    ):
        return None
    reason = str(getattr(review, "reason", "") or "").strip()
    streak = max(1, int(streak))
    confirm_rounds = max(1, int(confirm_rounds))
    evidence_files = report.get("evidence_files")
    return {
        "type": EventType.LIFE_PLAN_SIGNAL,
        "mode": mode,
        "signal": "reconsider",
        "reason": reason,
        "streak": streak,
        "confirm_rounds": confirm_rounds,
        "confirmed": mode == "active" and streak >= confirm_rounds,
        "evidence_files": evidence_files if isinstance(evidence_files, list) else [],
    }


def _review_scope_change_reason(review: ReviewDecision) -> str:
    """Return why a Reviewer ``continue`` needs Manager/Planner arbitration.

    ``continue`` is the fast same-mission repair lane.  A Reviewer that asks for
    a new/scoped mission, a replacement plan, or a changed task contract must
    not silently gain authorization by having its prose pasted into the next
    Engineer prompt.  Structured ``plan_signal=reconsider`` is authoritative;
    the conservative prose matcher protects older/misclassified verdicts.
    """
    if str(getattr(review, "status", "") or "").strip().lower() != "continue":
        return ""
    report = getattr(review, "planner_report", None)
    report = report if isinstance(report, dict) else {}
    if str(report.get("plan_signal") or "").strip().lower() == "reconsider":
        # Dynamic-plan mode owns structured reconsider cadence. Immediate
        # arbitration is reserved for explicit replan_requested or legacy prose
        # that asks to cross the current mission boundary.
        return ""

    candidate_parts = (str(getattr(review, "next_action", "") or ""),)
    candidate = "\n".join(part for part in candidate_parts if part.strip())
    if not candidate:
        return ""
    for pattern in _REVIEW_SCOPE_CHANGE_PATTERNS:
        if pattern.search(candidate):
            return (
                "Reviewer guidance requests work outside the current mission's "
                "same-scope repair lane; Manager authorization and Planner task "
                "replacement are required."
            )
    return ""


def _promote_scope_change_to_replan(
    review: ReviewDecision,
    *,
    reason: str,
) -> None:
    """Record harness arbitration without rewriting the Reviewer verdict."""
    control = dict(getattr(review, "harness_control", {}) or {})
    control["force_replan"] = True
    control["reason"] = reason
    # This is not necessarily an upstream-stage defect, but it still needs the
    # Manager's stage-boundary ruling before L4 replaces the mission.  A safe
    # Manager failure is HOLD, after which Planner can replan in the same stage.
    control["stage_reconciliation_required"] = True
    control["mission_scope_change_required"] = True
    review.harness_control = control


def _upstream_stage_reconciliation_target(
    review: ReviewDecision,
    *,
    workdir: Path,
) -> str:
    """Return an earlier broken stage that must be adjudicated by Manager.

    A Reviewer can discover that a run-stage mission is invalid because a
    benchmark/plan artifact is broken. Continuing another Engineer round under
    the later stage lets that Engineer bypass Manager stage ownership and may
    execute work that the latest review explicitly forbids.  Detect both a
    future structured field and the currently deployed prose form.
    """
    report = getattr(review, "planner_report", None)
    report = report if isinstance(report, dict) else {}
    control = dict(getattr(review, "harness_control", {}) or {})
    candidate = str(
        control.get("earliest_broken_stage")
        or report.get("earliest_broken_stage")
        or ""
    ).strip().lower()
    text_parts = [
        str(getattr(review, "reason", "") or ""),
        str(getattr(review, "next_action", "") or ""),
    ]
    if not candidate:
        joined = "\n".join(text_parts)
        for pattern in _EARLIEST_BROKEN_STAGE_PATTERNS:
            match = pattern.search(joined)
            if match:
                candidate = match.group(1).strip().lower().replace("-", "_")
                break
    if not candidate:
        return ""
    try:
        from ..skills.stage_machine import (
            _active_vertical_checklist_defs,
            current_stage,
        )

        active_stage = current_stage(workdir).strip().lower().replace("-", "_")
        stage_order, _items = _active_vertical_checklist_defs(workdir)
        order = [str(stage).strip().lower().replace("-", "_") for stage in stage_order]
    except Exception:  # noqa: BLE001 - uncertain stage identity must not reroute
        return ""
    if (
        candidate not in order
        or active_stage not in order
        or order.index(candidate) >= order.index(active_stage)
    ):
        return ""

    control["force_replan"] = True
    control["reason"] = str(getattr(review, "reason", "") or "")
    control["stage_reconciliation_required"] = True
    control["earliest_broken_stage"] = candidate
    review.harness_control = control
    return candidate


def _apply_round_secret_guard(
    *,
    workdir: Path,
    modified_since: float,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[SecretScrubReport, str]:
    report = scrub_recent_text_artifacts(
        workdir,
        modified_since=modified_since,
        known_values=known_secret_values(),
    )
    if not report.changed and not report.errors and not report.truncated:
        return report, ""
    if on_event:
        on_event({
            "type": EventType.ROUND_SECRET_REDACTED,
            "round_index": round_index,
            "round_max": round_max,
            "redacted_paths": list(report.redacted_paths),
            "replacement_count": report.replacement_count,
            "scanned_files": report.scanned_files,
            "scan_errors": list(report.errors),
            "truncated": report.truncated,
            "operator_alert": bool(report.errors or report.truncated),
        })
    if not report.changed and not report.truncated and not report.errors:
        return report, ""
    lines = [
        "SECURITY GUARD (authoritative artifact hygiene):",
    ]
    if report.changed:
        lines.extend((
            f"- Redacted {report.replacement_count} credential occurrence(s) "
            f"from {len(report.redacted_paths)} changed file(s) before review.",
            "- Files: " + ", ".join(report.redacted_paths),
            "- Revalidate any dependent hashes/provenance; this round is not "
            "complete until the scrubbed artifacts are internally consistent.",
        ))
    if report.truncated:
        lines.append(
            "- Coverage incomplete: at least one recently modified text artifact "
            "exceeded the live-scan size limit. Do not certify completion until "
            "the credential exposure risk is checked."
        )
    if report.errors:
        lines.append(
            "- Coverage incomplete: secret scan errors occurred for "
            + "; ".join(report.errors)
            + ". Do not certify completion until those files are checked."
        )
    return report, "\n".join(lines)


def _review_progress_class(review: ReviewDecision) -> str:
    value = str(getattr(review, "progress_class", "") or "").strip().lower()
    if value in _DECISION_PROGRESS_CLASSES | _NONDECISION_PROGRESS_CLASSES:
        return value
    report = getattr(review, "planner_report", None)
    return (
        "none"
        if isinstance(report, dict) and report.get("forward_progress") is False
        else "evidence"
    )


def _next_decision_stall_streak(
    review: ReviewDecision,
    current_streak: int,
) -> int:
    if review.status != "continue":
        return 0
    if _review_progress_class(review) in _DECISION_PROGRESS_CLASSES:
        return 0
    return max(0, int(current_streak)) + 1


def _pause_decision_clock(last_progress_at: float, waited_seconds: float) -> float:
    return float(last_progress_at) + max(0.0, float(waited_seconds or 0.0))


def _run_background_wait(
    *,
    workdir: Path,
    task_id: str,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[str, float]:
    if on_event:
        on_event({
            "type": "round.background_wait.started",
            "round_index": round_index,
            "round_max": round_max,
            "task_id": task_id,
            "text": f"yielding to supervised subagent cadence: {task_id}",
        })
    try:
        wait_reason, waited_s = wait_for_subagent_cadence(workdir, task_id)
    except Exception as exc:  # noqa: BLE001 — a wait must never break the loop
        wait_reason, waited_s = f"error:{type(exc).__name__}", 0.0
    if on_event:
        on_event({
            "type": "round.background_wait.completed",
            "round_index": round_index,
            "round_max": round_max,
            "task_id": task_id,
            "text": (
                f"resumed after {waited_s:.0f}s ({wait_reason}) waiting on {task_id}"
            ),
        })
    return wait_reason, waited_s


def _run_external_work_wait(
    *,
    workdir: Path,
    work_id: str,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[str, float]:
    if on_event:
        on_event({
            "type": "round.external_work_wait.started",
            "round_index": round_index,
            "round_max": round_max,
            "work_id": work_id,
            "text": f"yielding to external-work cadence: {work_id}",
        })
    try:
        wait_reason, waited_s = wait_for_external_work_cadence(workdir, work_id)
    except Exception as exc:  # noqa: BLE001 — a wait must never break the loop
        wait_reason, waited_s = f"error:{type(exc).__name__}", 0.0
    if on_event:
        on_event({
            "type": "round.external_work_wait.completed",
            "round_index": round_index,
            "round_max": round_max,
            "work_id": work_id,
            "reason": wait_reason,
            "text": (
                f"resumed after {waited_s:.0f}s ({wait_reason}) waiting on {work_id}"
            ),
        })
    return wait_reason, waited_s


def _review_wait_rejection(
    workdir: Path,
    task_id: str,
) -> tuple[str, str]:
    return inspect_wait_target(workdir, task_id)


__all__ = [
    "_review_event_payload",
    "_normalize_dynamic_plan_mode",
    "_plan_signal_event",
    "_review_scope_change_reason",
    "_promote_scope_change_to_replan",
    "_upstream_stage_reconciliation_target",
    "_apply_round_secret_guard",
    "_review_progress_class",
    "_next_decision_stall_streak",
    "_pause_decision_clock",
    "_run_background_wait",
    "_run_external_work_wait",
    "_review_wait_rejection",
    "_DECISION_PROGRESS_CLASSES",
    "_NONDECISION_PROGRESS_CLASSES",
]
