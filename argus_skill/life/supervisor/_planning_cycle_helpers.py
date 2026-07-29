"""Free helpers + mutable scratch state for one continuous-planner cycle.

``_PlanCycleState`` is threaded through the ``_plan_next_work`` lifecycle
phase mixins in ``_planning_cycle_intake.py``, ``_planning_cycle_verdict.py``,
``_planning_cycle_completion.py``, and ``_planning_cycle_enqueue.py``. It is
process-local scratch state for a single planning cycle call, never
persisted.

The free functions below operate on Reviewer-authored dynamic-plan revision
requests and the persisted research-target completion gate; they have no
``self`` dependency and are reused by more than one phase.
"""

from __future__ import annotations

from typing import Any

from ..memory import BacklogItem


def _revision_reason(revision_request: dict[str, Any]) -> str:
    for key in ("review_reason", "reason", "stop_reason", "summary"):
        value = str(revision_request.get(key) or "").strip()
        if value:
            return value
    # A replan request is itself a durable reason to replace the active plan.
    # Returning an empty string makes the atomic revision reject after Planner
    # already produced a valid replacement, then reruns the refuted item.
    return "Reviewer requested replacement of the active plan"


def _render_revision_request(
    revision_request: dict[str, Any],
    active_items: list[BacklogItem],
) -> str:
    lines = [
        "DYNAMIC PLAN REVISION REQUEST (Reviewer-authored, L4 decides):",
        "- reason: "
        + (
            _revision_reason(revision_request)
            or "Reviewer requested reconsideration; inspect the referenced "
            "artifacts and current CHECKPOINT.md before deciding."
        ),
        "- remaining active nodes:",
    ]
    lines.extend(
        f"  - {item.node_key or item.id}: [{item.status}] {item.title}" for item in active_items
    )
    lines.append(
        "Return a complete replacement batch for the remaining active nodes. "
        "Completed nodes are immutable. Do not return project_done. Exception: if "
        "current_stage itself makes the prerequisite repair illegal, return "
        "waiting=true with a waiting_contract whose "
        "stage_reconciliation_required=true; emit no replacement tasks and let the "
        "Manager decide HOLD versus ROLLBACK. Never use this exception for polling "
        "or an ordinary implementation blocker."
    )
    return "\n".join(lines)


def _research_project_done_issue(
    project_root: object,
    journal_entries: list[Any],
) -> str:
    """Require a current-target final Reviewer ``done`` before Planner success."""
    from ...core.research_contract import (
        resolve_research_target_contract,
        resolve_research_target_set_at,
    )

    target_contract = resolve_research_target_contract(project_root)
    target_level = target_contract.selected_level
    if target_contract.required and target_level is None:
        return "missing_research_target_level"
    target_set_at = resolve_research_target_set_at(project_root) or 0.0
    for entry in reversed(journal_entries):
        if str(getattr(entry, "kind", "") or "") not in {
            "mission_complete",
            "mission_replan_requested",
        }:
            continue
        try:
            entry_ts = float(getattr(entry, "ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if entry_ts < target_set_at:
            break
        extra = getattr(entry, "extra", None)
        if not isinstance(extra, dict):
            continue
        if (
            str(extra.get("scope") or "").strip().lower() == "final_submission"
            and extra.get("final_submission_certified") is True
        ):
            return ""
    if target_level is None:
        return ""
    return f"missing_{target_level}_reviewer_certification"


def _staged_goal_completion_issue(project_root: object) -> str:
    """Require the ordinary Reviewer/Manager final-stage certificate."""
    from ...skills.stage_machine import current_stage
    from ...skills.vertical_select import (
        resolve_vertical,
        vertical_has_current_completion_certificate,
    )
    from ...verticals._base import (
        load_vertical,
        vertical_checklist_stage_order,
        vertical_completion_contract_version,
        vertical_completion_gate,
    )

    try:
        vertical = resolve_vertical(project_root)
        module = load_vertical(vertical, project_root=project_root)
        if vertical_completion_gate(module) != "none":
            return ""
        stages = vertical_checklist_stage_order(module)
        if not stages or vertical_has_current_completion_certificate(
            project_root,
            vertical,
        ):
            return ""
        contract_version = vertical_completion_contract_version(module)
        contract_suffix = ""
        if contract_version > 0:
            from ...skills.stage_machine import completion_contract_fingerprint

            contract_sha256 = completion_contract_fingerprint(
                project_root,
                stages[-1],
                version=contract_version,
            )
            contract_suffix = f", contract=v{contract_version}:{contract_sha256}"
        return (
            f"{vertical} final-stage Goal Gate is not Reviewer-certified "
            f"(current_stage={current_stage(project_root)}{contract_suffix})"
        )
    except Exception:  # noqa: BLE001
        return "staged Goal Gate could not be resolved"


def goal_gate_task_title(project_root: object) -> str:
    """Name the Goal Gate mission after the stage it has to finish.

    "Complete and certify the current Goal Gate" was the title of every such
    mission regardless of stage. On a single-stage vertical like `software`
    that is the mission which does *all* the work, so an operator watching the
    queue saw a task named after certification while the Engineer was actually
    writing the code — observed on all twelve runs on 2026-07-26.

    Naming the stage also makes the deduplication signature stage-specific,
    which is more correct: the gate task for `delivery` and the gate task for
    `submission` are different work, not a repeat.
    """
    from ...skills.stage_machine import current_stage

    try:
        stage = str(current_stage(project_root) or "").strip()
    except Exception:  # noqa: BLE001 — the generic title is always valid
        stage = ""
    return (
        f"Finish and certify the {stage} stage"
        if stage
        else "Complete and certify the current Goal Gate"
    )


class _PlanCycleState:
    """Mutable scratch state threaded through one ``_plan_next_work`` call."""

    def __init__(self, revision_request: dict[str, Any] | None) -> None:
        self.revision_request: dict[str, Any] | None = (
            dict(revision_request) if isinstance(revision_request, dict) else None
        )

        # Set by the intake/gate phase.
        self.operator_messages: list[str] = []
        self.revision_active_items: list[BacklogItem] = []
        self.expected_plan_id: str = ""
        self.expected_plan_version: int = 0
        self.manager_intent: Any = None

        # Set by the planner-invocation phase.
        self.subagent_family_failures: dict[str, Any] = {}
        self.verdict: Any = None

        # Set by the dedupe/enqueue phases.
        self.existing_items: list[BacklogItem] = []
        self.seen_signatures: dict[tuple[str, ...], BacklogItem] = {}
        self.active_base_signatures: dict[tuple[str, ...], BacklogItem] = {}
        self.terminal_blocker_fingerprints: dict[str, BacklogItem] = {}
        self.recent_failures: dict[Any, Any] = {}
        self.added_titles: list[str] = []
        self.added_impact_scores: list[int] = []
        self.skipped_duplicate_titles: list[str] = []
        self.skipped_recent_failure_titles: list[str] = []
        self.skipped_subagent_family_failure_titles: list[str] = []
        self.new_plan_id: str = ""
        self.new_plan_version: int = 1
        self.key_map: dict[str, str] = {}
        self.pending_items: list[tuple[Any, Any]] = []


__all__ = [
    "_PlanCycleState",
    "_render_revision_request",
    "_research_project_done_issue",
    "_staged_goal_completion_issue",
    "_revision_reason",
]
