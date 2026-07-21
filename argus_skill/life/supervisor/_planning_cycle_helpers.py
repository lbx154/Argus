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


def _revision_context_refs(revision_request: dict[str, Any]) -> list[dict[str, str]]:
    report = revision_request.get("planner_report")
    report = report if isinstance(report, dict) else {}
    raw_refs = report.get("evidence_files")
    if not isinstance(raw_refs, list):
        return []
    refs: list[dict[str, str]] = []
    for raw in raw_refs[:8]:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        refs.append({
            "kind": "artifact",
            "ref": path[:400],
            "why": str(raw.get("why") or "").strip()[:600],
            "content_hash": str(raw.get("content_hash") or "").strip()[:128],
        })
    return refs


def _revision_reason(revision_request: dict[str, Any]) -> str:
    report = revision_request.get("planner_report")
    report = report if isinstance(report, dict) else {}
    return str(
        revision_request.get("review_reason")
        or report.get("plan_signal_reason")
        or ""
    ).strip()


def _render_revision_request(
    revision_request: dict[str, Any],
    active_items: list[BacklogItem],
) -> str:
    report = revision_request.get("planner_report")
    report = report if isinstance(report, dict) else {}
    lines = [
        "DYNAMIC PLAN REVISION REQUEST (Reviewer-authored, L4 decides):",
        "- reason: " + (
            _revision_reason(revision_request)
            or "Reviewer requested reconsideration; inspect the referenced "
            "artifacts and current CHECKPOINT.md before deciding."
        ),
        "- remaining active nodes:",
    ]
    lines.extend(
        f"  - {item.node_key or item.id}: [{item.status}] {item.title}"
        for item in active_items
    )
    refs = _revision_context_refs(revision_request)
    if refs:
        lines.append("- evidence files to open before replanning:")
        lines.extend(f"  - {ref['ref']}: {ref['why']}" for ref in refs)
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
    """Require a current-target Reviewer completion before Planner success."""
    from ...core.research_contract import (
        adapt_legacy_research_result_payload,
        resolve_research_target_contract,
        resolve_research_target_set_at,
    )

    target_contract = resolve_research_target_contract(project_root)
    target_level = target_contract.selected_level
    if target_contract.required and target_level is None:
        return "missing_research_target_level"
    if target_level is None:
        return ""
    target_set_at = resolve_research_target_set_at(project_root) or 0.0
    for entry in reversed(journal_entries):
        if str(getattr(entry, "kind", "") or "") != "mission_complete":
            continue
        try:
            entry_ts = float(getattr(entry, "ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if entry_ts < target_set_at:
            break
        extra = getattr(entry, "extra", None)
        if (
            isinstance(extra, dict)
            and str(extra.get("scope") or "").strip().lower() == "bounded"
        ):
            continue
        # ``mission_complete`` already means the independent Reviewer returned
        # done. Keep the structured result for Planner memory, but do not let the
        # harness reinterpret scientific labels into a second verdict.
        if adapt_legacy_research_result_payload(extra) is not None:
            return ""
    return f"missing_{target_level}_reviewer_certification"


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

        # Set by the verdict-normalization phase.
        self.planner_cost_usd: float = 0.0
        self.schema_repair_details: dict[str, Any] = {}

        # Set by the dedupe/enqueue phases.
        self.existing_items: list[BacklogItem] = []
        self.seen_signatures: dict[tuple[str, ...], BacklogItem] = {}
        self.active_base_signatures: dict[tuple[str, str], BacklogItem] = {}
        self.recent_failures: dict[Any, Any] = {}
        self.added_titles: list[str] = []
        self.added_impact_scores: list[int] = []
        self.skipped_duplicate_titles: list[str] = []
        self.skipped_recent_failure_titles: list[str] = []
        self.skipped_subagent_family_failure_titles: list[str] = []
        self.new_plan_id: str = ""
        self.new_plan_version: int = 1
        self.revision_context_refs: list[dict[str, str]] = []
        self.key_map: dict[str, str] = {}
        self.pending_items: list[tuple[Any, Any]] = []


__all__ = [
    "_PlanCycleState",
    "_render_revision_request",
    "_research_project_done_issue",
    "_revision_context_refs",
    "_revision_reason",
]
