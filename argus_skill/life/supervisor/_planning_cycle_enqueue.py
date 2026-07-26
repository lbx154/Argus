"""Planning-cycle phase: dedupe index, pending-item construction, and commit.

Covers everything after the planner has returned a trusted, non-empty
``new_tasks`` batch: building the existing-backlog dedupe index, the two-pass
DAG-aware pending-item construction (dedupe / recent-failure / subagent-
family-failure / authorization skips, then intra-batch dep-key resolution),
the revision-vs-non-revision commit path, and the final planner-verdict
emission.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ..memory import BacklogItem
from ._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
    PLANNER_DEDUP_STATUSES,
    REPLAN_FILTER_REJECTION_LIMIT,
)
from ._helpers import (
    _entry_task_signature,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
)
from ._planning_cycle_helpers import _PlanCycleState, _revision_reason

log = logging.getLogger(__name__)


class PlanningCycleEnqueueMixin:
    """Dedupe index, pending-item construction, commit, and final emission."""

    def _pc_build_dedupe_index(self, state: _PlanCycleState) -> Any | None:
        try:
            state.existing_items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            state.existing_items = []

        seen_signatures: dict[tuple[str, ...], BacklogItem] = {}
        active_base_signatures: dict[tuple[str, str], BacklogItem] = {}
        revision_active_ids = {item.id for item in state.revision_active_items}
        for existing in state.existing_items:
            if existing.id in revision_active_ids:
                continue
            if existing.status not in PLANNER_DEDUP_STATUSES:
                continue
            signature = _planner_task_signature(
                existing.title,
                existing.objective,
                acceptance_check=existing.acceptance_check,
                context_refs=list(existing.context_refs or []),
                scope=self._planner_scope_from_item(existing),
                stage_closing=self._item_requires_independent_review(existing),
            )
            if existing.status != "done":
                active_base_signatures[signature[:2]] = existing
                seen_signatures[signature] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        state.seen_signatures = seen_signatures
        state.active_base_signatures = active_base_signatures
        state.recent_failures = self._recent_no_progress_failures()
        state.new_plan_id = f"plan-{BacklogItem.new_id()}"
        state.new_plan_version = (
            state.expected_plan_version + 1 if state.revision_request is not None else 1
        )
        return None

    def _gate_reproposal_is_not_a_duplicate(self, task: Any, duplicate_item: Any) -> bool:
        """Whether a stage-closing proposal escapes the duplicate filter.

        Review semantics are part of task identity. A prior ordinary
        or self-reviewed task cannot satisfy a later stage-closing
        certification request, even when its prose is identical.

        Nor can a COMPLETED one. `done` means the mission finished,
        not that the gate closed — a stage-closing task can run,
        satisfy its own review, and still leave the gate uncertified.
        When that happened the Planner re-proposed the gate and this
        filter skipped it as a "duplicate completed task" every cycle,
        leaving nothing pending and nothing to do but back off and
        retry. Caught live on a clean project: 5 identical verdicts,
        4 skips, an empty backlog and no exit, because "a done task
        has this signature" is not a condition that changes.

        If the Planner is asking for the gate again, the previous
        attempt evidently did not close it, or the campaign would
        have completed. A still-pending or running duplicate is a
        genuine duplicate and is still filtered, so concurrent copies
        of in-flight work remain impossible.
        """
        if not bool(getattr(task, "stage_closing", False)):
            return False
        return duplicate_item.status == "done" or not (
            self._item_requires_independent_review(duplicate_item)
        )

    def _pc_build_pending_items(self, state: _PlanCycleState) -> Any | None:
        # Add new tasks to the backlog.
        #
        # Two passes so a planner-emitted DAG can be wired up before anything is
        # enqueued. Pass 1 builds the surviving items (after dedup / recent-
        # failure skips, exactly as before) WITHOUT adding them yet, and records
        # each task's local ``key`` → real ``item.id`` in ``key_map``. Pass 2
        # resolves each task's ``deps`` (local keys → real ids) onto the item and
        # only then adds it. A flat task (no key/deps) flows through with an empty
        # dep list, so its enqueue is byte-for-byte identical to the old path.
        key_map: dict[str, str] = {}
        pending_items: list[tuple[Any, Any]] = []  # (task, item)
        for task in state.verdict.new_tasks:
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                )
            signature = _planner_task_signature(
                task.title,
                task.objective,
                acceptance_check=str(getattr(task, "acceptance_check", "") or ""),
                context_refs=list(getattr(task, "context_refs", []) or []),
                scope=str(getattr(task, "scope", "") or ""),
                stage_closing=bool(getattr(task, "stage_closing", False)),
            )
            duplicate_item = state.active_base_signatures.get(
                signature[:2]
            ) or state.seen_signatures.get(signature)
            if duplicate_item is not None and self._gate_reproposal_is_not_a_duplicate(
                task, duplicate_item
            ):
                duplicate_item = None
            if duplicate_item is not None:
                state.skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate pending/running task"
                )
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "evidence": task.evidence,
                        "matched_item_id": duplicate_item.id,
                        "matched_status": duplicate_item.status,
                        "reason": duplicate_reason,
                    }
                )
                continue
            recent_failure = state.recent_failures.get(signature)
            if recent_failure is not None:
                state.skipped_recent_failure_titles.append(task.title)
                failure_extra = getattr(recent_failure, "extra", {}) or {}
                failure_signature = _entry_task_signature(recent_failure)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "evidence": task.evidence,
                        "matched_item_id": failure_extra.get("item_id"),
                        "matched_title": recent_failure.title,
                        "matched_status": failure_extra.get("terminal_status")
                        or failure_extra.get("status"),
                        "matched_stop_reason": failure_extra.get("stop_reason")
                        or failure_extra.get("failure_reason"),
                        "matched_signature": (
                            {
                                "title": failure_signature[0],
                                "objective": failure_signature[1],
                            }
                            if failure_signature is not None
                            else None
                        ),
                        "skip_category": "recent_no_progress_failure",
                        "reason": "recent no_progress failure",
                    }
                )
                continue
            family_failure = next(
                (
                    ff
                    for ff in state.subagent_family_failures.values()
                    if self._task_mentions_family(task, ff.family)
                ),
                None,
            )
            if family_failure is not None:
                state.skipped_subagent_family_failure_titles.append(task.title)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "evidence": task.evidence,
                        "matched_family": family_failure.family,
                        "matched_streak": family_failure.streak,
                        "matched_last_task_id": family_failure.last_task_id,
                        "matched_last_state": family_failure.last_state,
                        "matched_last_reason": family_failure.last_reason,
                        "skip_category": "recent_subagent_family_failure",
                        "reason": (
                            f"subagent family {family_failure.family!r} has failed "
                            f"{family_failure.streak} times in a row unresolved"
                        ),
                    }
                )
                continue
            item_id = BacklogItem.new_id()
            try:
                authorization_id, authorization_action = self._validated_task_authorization(task)
            except (OSError, TypeError, ValueError) as exc:
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "evidence": task.evidence,
                        "reason": str(exc),
                        "skip_category": "invalid_authorization",
                    }
                )
                continue
            item = BacklogItem.new(
                item_id=item_id,
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                plan_id=state.new_plan_id,
                plan_version=state.new_plan_version,
                node_key=str(getattr(task, "key", "") or item_id),
                context_refs=list(getattr(task, "context_refs", []) or []),
                acceptance_check=str(
                    getattr(task, "acceptance_check", "") or getattr(task, "evidence", "")
                ),
                non_goals=list(getattr(task, "non_goals", []) or []),
                authorization_id=authorization_id,
                authorization_action=authorization_action,
            )
            # Reserve the signature now so a later sibling in the SAME batch
            # with an identical title/objective still de-dupes against this
            # one (matches the old single-pass behaviour). The item is not
            # added to the backlog until pass 2.
            state.seen_signatures[signature] = item
            if getattr(task, "key", ""):
                key_map[task.key] = item.id
            pending_items.append((task, item))

        state.key_map = key_map
        state.pending_items = pending_items
        return None

    def _pc_commit_pending_items(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version
        manager_intent = state.manager_intent

        # Pass 2: resolve local dep keys to real item ids, then enqueue. Only
        # intra-batch deps are supported — a key the planner referenced but did
        # not define in THIS batch (typo, or an unsupported cross-cycle ref) is
        # dropped with a warning so a stray key cannot wedge the item forever.
        for task, item in state.pending_items:
            task_deps = list(getattr(task, "deps", []) or [])
            if task_deps:
                resolved_ids, unresolved_keys = _resolve_task_dep_ids(task_deps, state.key_map)
                item.deps = resolved_ids
                if unresolved_keys:
                    log.warning(
                        "life supervisor: dropping unresolved planner dep "
                        "key(s) %s for task %r (only same-batch new_tasks deps "
                        "are supported)",
                        unresolved_keys,
                        item.title,
                    )
        if revision_request is None and state.pending_items:
            try:
                self.memory.backlog.add_many([item for _task, item in state.pending_items])
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_ERROR,
                        "cycle": self._planning_cycles,
                        "error": f"planner DAG commit rejected: {type(exc).__name__}: {exc}",
                    }
                )
                self._emit_status("planner DAG rejected before commit; retrying after backoff")
                self._enter_idle_backoff()
                return PLAN_ERROR
            for task, item in state.pending_items:
                state.added_titles.append(item.title)
                state.added_impact_scores.append(task.impact_score)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_ADDED,
                        "item_id": item.id,
                        "title": item.title,
                        "objective": item.objective,
                        "deps": list(item.deps),
                        "priority": item.priority,
                        "branch_id": item.id,
                        "parent_branch_id": item.deps[0] if item.deps else None,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "manager_intent": manager_intent,
                        "plan_id": item.plan_id,
                        "plan_version": item.plan_version,
                        "node_key": item.node_key,
                    }
                )

        if revision_request is not None and state.pending_items:
            replacement_items = [item for _task, item in state.pending_items]
            try:
                revision_result = self.memory.backlog.apply_plan_revision(
                    expected_plan_id=expected_plan_id,
                    expected_version=expected_plan_version,
                    new_plan_id=state.new_plan_id,
                    new_version=state.new_plan_version,
                    supersede_item_ids=[item.id for item in state.revision_active_items],
                    new_items=replacement_items,
                    reason=_revision_reason(revision_request),
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    {
                        "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "expected_plan_id": expected_plan_id,
                        "expected_plan_version": expected_plan_version,
                    }
                )
                return PLAN_ERROR
            for item_id in revision_result.superseded_ids:
                self._emit(
                    {
                        "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                        "item_id": item_id,
                        "plan_id": expected_plan_id,
                        "plan_version": expected_plan_version,
                        "superseded_by_plan_id": state.new_plan_id,
                        "reason": _revision_reason(revision_request),
                    }
                )
            for task, item in state.pending_items:
                state.added_titles.append(item.title)
                state.added_impact_scores.append(task.impact_score)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_ADDED,
                        "item_id": item.id,
                        "title": item.title,
                        "objective": item.objective,
                        "deps": list(item.deps),
                        "priority": item.priority,
                        "branch_id": item.id,
                        "parent_branch_id": item.deps[0] if item.deps else None,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "manager_intent": manager_intent,
                        "plan_id": item.plan_id,
                        "plan_version": item.plan_version,
                        "node_key": item.node_key,
                    }
                )
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_COMMITTED,
                    "old_plan_id": expected_plan_id,
                    "old_plan_version": expected_plan_version,
                    "new_plan_id": state.new_plan_id,
                    "new_plan_version": state.new_plan_version,
                    "superseded_item_ids": list(revision_result.superseded_ids),
                    "added_item_ids": list(revision_result.added_ids),
                }
            )

        if revision_request is not None and not state.pending_items:
            requested_item_id = str(revision_request.get("item_id") or "")
            requested_item = next(
                (item for item in state.revision_active_items if item.id == requested_item_id),
                None,
            )
            attempts = int(getattr(requested_item, "replan_rejections", 0) or 0) + 1
            if requested_item is not None:
                self.memory.backlog.update(
                    requested_item.id,
                    replan_rejections=attempts,
                    last_error=(
                        "planner replacement rejected because all proposed "
                        f"tasks were filtered (attempt {attempts})"
                    ),
                )
            terminal = attempts >= REPLAN_FILTER_REJECTION_LIMIT
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "all replacement tasks were filtered",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                    "attempts": attempts,
                    "terminal": terminal,
                }
            )
            if terminal and requested_item is not None:
                self.memory.backlog.mark_failed(
                    requested_item.id,
                    error=(
                        f"filtered replacement circuit breaker opened after {attempts} attempts"
                    ),
                )
                sleep_s = self._enter_idle_backoff()
                self._emit_status(
                    "planner replacement remained empty after bounded retries; "
                    "current node failed closed and awaits new evidence"
                )
                self._suggested_sleep_s = max(self._suggested_sleep_s, sleep_s)
                return PLAN_TERMINAL_IDLE
        return None

    def _pc_emit_final_verdict(self, state: _PlanCycleState) -> Any:
        verdict = state.verdict
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=True if state.added_titles else PLAN_RETRY,
            cycle=self._planning_cycles,
            project_done=verdict.project_done,
            reason=verdict.reason,
            task_count=len(verdict.new_tasks),
            enqueued_tasks=len(state.added_titles),
            skipped_duplicate_tasks=len(state.skipped_duplicate_titles),
            skipped_recent_failure_tasks=len(state.skipped_recent_failure_titles),
            skipped_subagent_family_failure_tasks=len(state.skipped_subagent_family_failure_titles),
            enqueued_titles=state.added_titles,
            enqueued_impact_scores=state.added_impact_scores,
            skipped_duplicate_titles=state.skipped_duplicate_titles,
            skipped_recent_failure_titles=state.skipped_recent_failure_titles,
            skipped_subagent_family_failure_titles=(state.skipped_subagent_family_failure_titles),
            stuck_subagent_families={
                family: failure.streak for family, failure in state.subagent_family_failures.items()
            },
            manager_intent=state.manager_intent,
        )
        if not delivered:
            return PLAN_RETRY
        if not state.added_titles:
            self._enter_idle_backoff()
            self._emit_status("planner: all proposed tasks were filtered; retrying after backoff")
            return PLAN_RETRY
        self._clear_manager_planner_feedback()
        # Real new work was queued: clear the no-work backoff so the next cycle
        # runs promptly.
        self._reset_idle_backoff()
        return True


__all__ = ["PlanningCycleEnqueueMixin"]
