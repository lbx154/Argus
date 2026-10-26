"""Planning-cycle phase: dedupe index, pending-item construction, and commit.

Covers everything after the planner has returned a trusted, non-empty
``new_tasks`` batch: building the existing-backlog dedupe index, the two-pass
DAG-aware pending-item construction (dedupe / authorization skips, then
intra-batch dep-key resolution),
the revision-vs-non-revision commit path, and the final planner-verdict
emission.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ..memory import BacklogItem, planner_may_retire_park
from ._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLANNER_DEDUP_STATUSES,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
)
from ._helpers import (
    _normalize_task_dep_key,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
    _unique_normalized_task_key_aliases,
)
from ._planning_cycle_helpers import _PlanCycleState, _revision_reason

log = logging.getLogger(__name__)


def _record_filtered_task(
    state: _PlanCycleState,
    *,
    title: str,
    category: str,
    reason: str,
) -> None:
    state.skipped_task_feedback.append({
        "title": str(title or "proposed task").strip(),
        "category": str(category or "filtered").strip(),
        "reason": str(reason or "filtered by Supervisor policy").strip(),
    })


def _render_filtered_task_feedback(state: _PlanCycleState) -> str:
    rows = state.skipped_task_feedback
    if not rows:
        return ""
    lines = ["Supervisor filtered every task in the previous Planner proposal:"]
    for row in rows[:8]:
        lines.append(
            f"- [{row['category']}] {row['title']}: {row['reason']}"
        )
    if len(rows) > 8:
        lines.append(f"- ... and {len(rows) - 8} additional filtered task(s)")
    return "\n".join(lines)


def _independent_review_forced() -> bool:
    return os.environ.get(
        "ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW", ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _stage_closing_forced() -> bool:
    return os.environ.get(
        "ARGUS_SKILL_FORCE_STAGE_CLOSING", ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _apply_planner_stage_request(
    *,
    state_root: Path,
    requested_stage: str,
    reason: str,
    evidence_root: Path,
) -> None:
    """Apply a Manager-owned Planner stage request."""
    from ...skills.stage_machine import (
        StageRollbackError,
        advance_stage,
        current_stage,
        rollback_stage,
    )

    current = current_stage(state_root)
    if requested_stage == current:
        return
    try:
        advance_stage(
            state_root,
            target_stage=requested_stage,
            reason=reason,
            advanced_by="manager:planner_request",
            evidence_root=evidence_root,
        )
    except ValueError as advance_error:
        try:
            rollback_stage(
                state_root,
                target_stage=requested_stage,
                reason=reason,
                rolled_back_by="manager:planner_request",
                evidence_root=evidence_root,
            )
        except StageRollbackError:
            raise
        except ValueError:
            raise advance_error


def _challenge_latency(challenge: dict[str, Any]) -> float:
    """Seconds from the Reviewer raising a challenge to the Planner's revision."""
    import time

    try:
        raised_at = float(challenge.get("raised_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, time.time() - raised_at) if raised_at > 0 else 0.0


class PlanningCycleEnqueueMixin:
    """Dedupe index, pending-item construction, commit, and final emission."""

    def _pc_build_dedupe_index(self, state: _PlanCycleState) -> Any | None:
        try:
            # Archived rows still name node ids/keys later tasks may depend on.
            state.existing_items = self.memory.backlog.history()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            state.existing_items = []

        seen_signatures: dict[tuple[str, ...], BacklogItem] = {}
        active_node_keys: dict[str, BacklogItem] = {}
        revision_active_ids = {item.id for item in state.revision_active_items}
        # Retirement is applied after the commit. A row this same verdict
        # retires is not a live duplicate of its replacement; counting it
        # filtered the replacement and then retired the original, so both
        # were lost (and in-batch dependents were wired to the retired row).
        retiring_ids = {
            str(item_id)
            for item_id, _reason in (getattr(state.verdict, "retire_tasks", ()) or ())
        }
        # The retirement cascade skips the pending rows that depend on a
        # retired one, so they are not live duplicates of their re-proposals
        # either.
        released_ids: set[str] = set()
        frontier = {
            existing.id
            for existing in state.existing_items
            if existing.id in retiring_ids
            and (existing.status == "pending" or planner_may_retire_park(existing))
        }
        while frontier:
            frontier = {
                existing.id
                for existing in state.existing_items
                if existing.status == "pending"
                and existing.id not in released_ids
                and frontier.intersection(existing.deps)
            }
            released_ids |= frontier
        for existing in state.existing_items:
            if existing.id in released_ids:
                continue
            if existing.id in revision_active_ids:
                continue
            if existing.id in retiring_ids and (
                existing.status == "pending" or planner_may_retire_park(existing)
            ):
                continue
            if existing.status not in PLANNER_DEDUP_STATUSES:
                continue
            signature = _planner_task_signature(
                existing.title,
                existing.objective,
                acceptance_check=existing.acceptance_check,
                context_refs=list(existing.context_refs or []),
                scope=(
                    self._planner_scope_from_item(existing)
                    or PLANNER_SCOPE_BOUNDED
                ),
                stage_closing=self._item_is_stage_closing(existing),
                require_independent_review=(
                    self._item_requires_independent_review(existing)
                ),
                skip_stage_transition=self._item_skips_stage_transition(existing),
                execution_workdir=str(existing.execution_workdir or ""),
            )
            seen_signatures[signature] = existing
            node_key = str(existing.node_key or "").strip()
            if node_key:
                active_node_keys[node_key] = existing

        state.seen_signatures = seen_signatures
        state.active_node_keys = active_node_keys
        state.new_plan_id = f"plan-{BacklogItem.new_id()}"
        state.new_plan_version = (
            state.expected_plan_version + 1 if state.revision_request is not None else 1
        )
        return None

    @staticmethod
    def _item_pipeline_stage(item: Any) -> str:
        """Stage tag persisted when a Planner task was enqueued."""
        for raw in getattr(item, "tags", []) or []:
            tag = str(raw or "").strip().lower()
            if tag.startswith("stage:"):
                return tag.split(":", 1)[1].strip()
        return ""

    def _gate_reproposal_is_not_a_duplicate(self, task: Any, duplicate_item: Any) -> bool:
        """Whether a stage-closing proposal escapes the duplicate filter.

        Review semantics are part of task identity. A prior ordinary task
        cannot satisfy a later stage-closing
        certification request, even when its prose is identical.
        """
        requires_review = bool(getattr(task, "stage_closing", False)) or bool(
            getattr(task, "require_independent_review", True)
        )
        if not requires_review:
            return False
        return not self._item_requires_independent_review(duplicate_item)

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
        planned_tasks = list(state.verdict.new_tasks)
        feedback_reader = getattr(self, "_load_manager_planner_feedback", None)
        manager_feedback = feedback_reader() if callable(feedback_reader) else {}
        manager_feedback = manager_feedback or {}
        feedback_diagnostic = str(
            manager_feedback.get("diagnostic") or ""
        ).strip()
        context_root = self._project_workdir()
        state_reader = getattr(self, "_artifact_root", None)
        state_root = state_reader() if callable(state_reader) else Path(context_root)
        requested_stage = str(
            getattr(state.verdict, "advance_to_stage", "") or ""
        ).strip()
        if requested_stage:
            try:
                _apply_planner_stage_request(
                    state_root=Path(state_root),
                    requested_stage=requested_stage,
                    reason=state.verdict.summary or "Planner requested stage transition",
                    evidence_root=Path(context_root).resolve(),
                )
            except Exception as exc:  # noqa: BLE001 - invalid requests replan safely
                failure_reason = f"{type(exc).__name__}: {exc}"
                stage = str(self._current_pipeline_stage() or "")
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "skip_category": "invalid_stage_transition_request",
                    "reason": failure_reason,
                    "requested_stage": requested_stage,
                })
                if not self._persist_manager_planner_feedback(
                    stage=stage,
                    reason=failure_reason,
                    diagnostic="stage_completion_gate_failed",
                ):
                    self._emit_status(
                        "failed to persist stage-transition rejection; retry later"
                    )
                    return PLAN_ERROR
                self._reset_idle_backoff()
                return PLAN_RETRY
        waypoint_states = getattr(self, "_waypoint_states", None)
        route = {entry.step.id: entry.step for entry in (waypoint_states() if callable(waypoint_states) else ())}
        for task_index, task in enumerate(planned_tasks):
            task = replace(task, context_refs=[], execution_workdir="")
            step = route.get(str(getattr(task, "waypoint_id", "") or ""))
            if step is None:
                # A step removed since the Planner read the route: an ordinary task.
                task = replace(task, waypoint_id="")
            elif step.text not in task.acceptance_check:
                # The Reviewer checks a step's task against the person's own words.
                task = replace(task, acceptance_check="\n".join(part for part in (
                    task.acceptance_check.strip(),
                    f"It fulfils the operator's required {step.label(False)}: «{step.text}».",
                ) if part))
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            sanitized_hypothesis = _sanitize_planner_task_text(task.hypothesis)
            sanitized_goal_contribution = _sanitize_planner_task_text(
                task.goal_contribution
            )
            sanitized_expected_regressions = _sanitize_planner_task_text(
                task.expected_regressions
            )
            sanitized_decision_rule = _sanitize_planner_task_text(task.decision_rule)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
                or sanitized_hypothesis != task.hypothesis
                or sanitized_goal_contribution != task.goal_contribution
                or sanitized_expected_regressions != task.expected_regressions
                or sanitized_decision_rule != task.decision_rule
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                    hypothesis=sanitized_hypothesis,
                    goal_contribution=sanitized_goal_contribution,
                    expected_regressions=sanitized_expected_regressions,
                    decision_rule=sanitized_decision_rule,
                )
            canonical_scope = self._normalize_planner_scope(
                getattr(task, "scope", "")
            )
            host_final_submission = bool(
                task_index == 0
                and feedback_diagnostic
                in {"final_certification_missing", "research_target_incomplete"}
            )
            host_stage_closing = bool(
                task_index == 0
                and feedback_diagnostic == "staged_goal_gate_incomplete"
            )
            if host_final_submission:
                canonical_scope = PLANNER_SCOPE_FINAL_SUBMISSION
            if (
                canonical_scope == PLANNER_SCOPE_FINAL_SUBMISSION
                and not self._final_submission_scope_applies(self._artifact_root())
            ):
                canonical_scope = PLANNER_SCOPE_BOUNDED
            canonical_acceptance = str(
                getattr(task, "acceptance_check", "")
                or getattr(task, "evidence", "")
                or ""
            )
            canonical_context_refs = list(getattr(task, "context_refs", []) or [])
            canonical_owns_paths = [
                path.strip().replace("\\", "/").strip("/")
                for path in getattr(task, "owns_paths", []) or []
            ]
            canonical_stage_closing = bool(
                canonical_scope == PLANNER_SCOPE_FINAL_SUBMISSION
                or _stage_closing_forced()
                or host_stage_closing
            )
            canonical_require_review = bool(
                canonical_stage_closing
                or _independent_review_forced()
                or getattr(task, "require_independent_review", True)
            )
            requested_parallel = bool(getattr(task, "parallel_safe", False))
            canonical_parallel = requested_parallel and not canonical_stage_closing
            if requested_parallel and canonical_stage_closing:
                # Reported at commit time, once per enqueued task, so
                # dedup-skipped replans do not repeat the event.
                state.dropped_parallel.append((task.title, "stage-closing work always runs alone"))
            task = replace(
                task,
                scope=canonical_scope,
                acceptance_check=canonical_acceptance,
                context_refs=canonical_context_refs,
                stage_closing=canonical_stage_closing,
                require_independent_review=canonical_require_review,
                skip_stage_transition=False,
                allow_skill_changes=False,
                parallel_safe=canonical_parallel,
                owns_paths=canonical_owns_paths,
                gpu_count=max(0, int(getattr(task, "gpu_count", 0) or 0)),
                cpu_count=max(1, int(getattr(task, "cpu_count", 1) or 1)),
            )
            from ...skills.vertical_select import resolve_vertical
            from ...verticals._base import load_vertical_contract

            policy_vertical = (
                str(getattr(task, "vertical", "") or "").strip()
                or resolve_vertical(state_root)
            )
            try:
                load_vertical_contract(policy_vertical, project_root=state_root)
            except LookupError:
                _record_filtered_task(
                    state,
                    title=task.title,
                    category="unknown_task_vertical",
                    reason=f"unknown Planner task vertical: {policy_vertical}",
                )
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "skip_category": "unknown_task_vertical",
                    "reason": f"unknown Planner task vertical: {policy_vertical}",
                })
                continue

            signature = _planner_task_signature(
                task.title,
                task.objective,
                acceptance_check=task.acceptance_check,
                context_refs=list(task.context_refs),
                scope=task.scope,
                stage_closing=canonical_stage_closing,
                require_independent_review=canonical_require_review,
                skip_stage_transition=bool(
                    getattr(task, "skip_stage_transition", False)
                ),
                execution_workdir=str(
                    getattr(task, "execution_workdir", "") or ""
                ),
            )
            planner_node_key = str(getattr(task, "key", "") or "").strip()
            node_key_duplicate = (
                state.active_node_keys.get(planner_node_key)
                if planner_node_key
                else None
            )
            duplicate_item = node_key_duplicate or state.seen_signatures.get(signature)
            if (
                duplicate_item is not None
                and node_key_duplicate is None
                and self._gate_reproposal_is_not_a_duplicate(task, duplicate_item)
            ):
                duplicate_item = None
            if duplicate_item is not None:
                if getattr(task, "key", ""):
                    key_map[task.key] = duplicate_item.id
                state.skipped_duplicate_titles.append(task.title)
                duplicate_reason = "duplicate pending/running task"
                if duplicate_item.status in {"pending", "running"}:
                    state.skipped_live_duplicate_titles.append(task.title)
                _record_filtered_task(
                    state,
                    title=task.title,
                    category="duplicate_task",
                    reason=duplicate_reason,
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
            item_id = BacklogItem.new_id()
            try:
                authorization_id, authorization_action = self._validated_task_authorization(task)
            except (OSError, TypeError, ValueError) as exc:
                _record_filtered_task(
                    state,
                    title=task.title,
                    category="invalid_authorization",
                    reason=str(exc),
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
                        "reason": str(exc),
                        "skip_category": "invalid_authorization",
                    }
                )
                continue
            manager_decision = self._manager_decision_evidence(
                state.manager_intent,
                task_vertical=str(getattr(task, "vertical", "") or ""),
            )
            task_tags = self._planner_task_tags(task)
            task_context_refs = list(getattr(task, "context_refs", []) or [])
            if manager_decision.get("vertical") == "argus_maintenance":
                task_tags.append("framework_maintenance")
                if "review:waived" in task_tags:
                    task_tags.remove("review:waived")
                if "review:required" not in task_tags:
                    task_tags.append("review:required")
                if getattr(task, "parallel_safe", False):
                    # The claim gate refuses framework maintenance outright;
                    # the flag would only mislead the digest and the Planner.
                    task = replace(task, parallel_safe=False)
                    state.dropped_parallel.append((
                        task.title,
                        "framework maintenance always runs alone",
                    ))
                evidence_reason = str(
                    getattr(task, "evidence", "")
                    or getattr(task, "hypothesis", "")
                    or task.objective
                ).strip()
                memory_root = Path(
                    getattr(getattr(self, "memory", None), "root", state_root)
                )
                task_context_refs.append({
                    "ref": str(memory_root / "events.jsonl"),
                    "kind": "runtime evidence",
                    "why": evidence_reason,
                })
                operator_context = Path(state_root) / "operator_context.jsonl"
                if operator_context.is_file():
                    task_context_refs.append({
                        "ref": str(operator_context),
                        "kind": "operator steering",
                        "why": evidence_reason,
                    })
            from ...verticals._data_domain import list_formal_data_domain_purposes

            formal_domains = list_formal_data_domain_purposes(
                state_root,
                learned_root=self._budget_global_root(),
            )
            if (
                manager_decision.get("learned_vertical_status") == "candidate"
                and manager_decision.get("vertical") not in formal_domains
                and "review:required" not in task_tags
            ):
                if "review:waived" in task_tags:
                    task_tags.remove("review:waived")
                task_tags.append("review:required")
            if "review:waived" in task_tags:
                self._emit({
                    "type": "life.review.waived",
                    "text": (
                        "independent review waived: Planner explicitly set "
                        "require_independent_review=false"
                    ),
                    "title": task.title,
                    "reason": str(state.verdict.summary or "Planner waiver"),
                })
            item = BacklogItem.new(
                item_id=item_id,
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=task_tags,
                iterate=True,
                plan_id=state.new_plan_id,
                plan_version=state.new_plan_version,
                node_key=str(getattr(task, "key", "") or item_id),
                context_refs=task_context_refs,
                acceptance_check=str(getattr(task, "acceptance_check", "") or ""),
                plan_hypothesis=str(getattr(task, "hypothesis", "") or ""),
                goal_contribution=str(
                    getattr(task, "goal_contribution", "") or ""
                ),
                expected_regressions=str(
                    getattr(task, "expected_regressions", "") or ""
                ),
                decision_rule=str(getattr(task, "decision_rule", "") or ""),
                execution_workdir=str(
                    getattr(task, "execution_workdir", "") or ""
                ),
                parallel_safe=bool(getattr(task, "parallel_safe", False)),
                owns_paths=list(getattr(task, "owns_paths", []) or []),
                gpu_count=max(0, int(getattr(task, "gpu_count", 0) or 0)),
                cpu_count=max(1, int(getattr(task, "cpu_count", 1) or 1)),
                non_goals=list(getattr(task, "non_goals", []) or []),
                original_objective=str(
                    getattr(self.config, "continuous_objective", "") or ""
                ),
                authorization_id=authorization_id,
                authorization_action=authorization_action,
                manager_decision=manager_decision,
                waypoint_id=str(getattr(task, "waypoint_id", "") or ""),
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

    @staticmethod
    def _manager_decision_evidence(
        intent: Any,
        *,
        task_vertical: str = "",
    ) -> dict[str, Any]:
        # Planner nodes are already subdivisions of the standing
        # Manager-approved campaign. Mark that inherited authority even when
        # the compact intent event has no optional routing fields.
        if not isinstance(intent, dict):
            intent = {}
        evidence = {
            "vertical": (
                str(task_vertical or "").strip()
                or str(intent.get("vertical") or "").strip()
            ),
            "stage": str(
                intent.get("stage") or intent.get("current_stage") or ""
            ).strip(),
            "workflow_mode": str(intent.get("workflow_mode") or "").strip(),
            "research_target_level": str(
                intent.get("research_target_level") or ""
            ).strip(),
            "learned_vertical_status": str(
                intent.get("learned_vertical_status") or ""
            ).strip(),
        }
        evidence = {key: value for key, value in evidence.items() if value}
        if task_vertical:
            evidence["route_source"] = "planner"
        evidence["routed"] = True
        return evidence

    def _pc_hold_children_of_filtered_siblings(self, state: _PlanCycleState) -> None:
        """Hold back tasks whose in-batch parent was filtered out of this batch.

        A dependency key that names a sibling task of the same verdict is real,
        required work. When a filter (policy, authorization) removed that
        sibling, its key never reached ``key_map``
        and pass 2 dropped it like a hallucinated key, so the child ran without
        its parent. Hold the child (and, transitively, whatever needs it) with
        a reason instead; the Planner sees it in the filtered-task feedback.
        A duplicate sibling is not lost: its key maps to the existing item.
        """
        batch_keys = {
            str(getattr(task, "key", "") or "").strip()
            for task in (getattr(state.verdict, "new_tasks", None) or [])
        } - {""}
        lost = {key for key in batch_keys if key not in state.key_map}
        if not lost:
            return
        persisted = {str(item.id) for item in state.existing_items} | {
            str(item.node_key)
            for item in state.existing_items
            if str(item.node_key or "").strip()
        }
        lost_normalized = {_normalize_task_dep_key(key) for key in lost}
        changed = True
        while changed:
            changed = False
            for task, item in list(state.pending_items):
                missing = [
                    dep
                    for dep in (getattr(task, "deps", []) or [])
                    if dep not in state.key_map
                    and dep not in persisted
                    and (dep in lost or _normalize_task_dep_key(dep) in lost_normalized)
                ]
                if not missing:
                    continue
                state.pending_items.remove((task, item))
                key = str(getattr(task, "key", "") or "").strip()
                if key:
                    state.key_map.pop(key, None)
                    lost.add(key)
                    lost_normalized.add(_normalize_task_dep_key(key))
                changed = True
                reason = (
                    "depends on task(s) of this plan that were not enqueued: "
                    + ", ".join(missing)
                )
                _record_filtered_task(
                    state,
                    title=task.title,
                    category="filtered_dependency",
                    reason=reason,
                )
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "dependency_keys": list(missing),
                        "skip_category": "filtered_dependency",
                        "reason": reason,
                    }
                )

    def _pc_commit_pending_items(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version
        manager_intent = state.manager_intent

        self._pc_hold_children_of_filtered_siblings(state)

        # Pass 2: resolve dep keys to real item ids, then enqueue. A later
        # planning cycle may naturally depend on a prior node, so include stable
        # node keys already persisted in the backlog. A key that names nothing
        # at all is dropped below; a key naming a filtered sibling of this
        # batch was already handled above by holding its dependents back.
        known_key_map = {
            str(item.id): str(item.id) for item in state.existing_items
        }
        historical_key_entries = [
            (str(item.node_key), str(item.id))
            for item in state.existing_items
            if str(item.node_key or "").strip()
        ]
        known_key_map.update(historical_key_entries)
        known_key_map.update(state.key_map)
        normalized_key_map = _unique_normalized_task_key_aliases(
            historical_key_entries
            + [(str(key), str(item_id)) for key, item_id in state.key_map.items()]
        )
        unresolved: list[tuple[str, list[str]]] = []
        for task, item in state.pending_items:
            task_deps = list(getattr(task, "deps", []) or [])
            if not task_deps:
                continue
            resolved_ids, unresolved_keys = _resolve_task_dep_ids(
                task_deps,
                known_key_map,
                normalized_key_map,
            )
            item.deps = resolved_ids
            normalized_deps = [
                dep
                for dep in task_deps
                if dep not in known_key_map
                and dep not in unresolved_keys
            ]
            if normalized_deps:
                log.info(
                    "planner dependency keys normalized",
                    extra={
                        "task_title": item.title,
                        "dependency_keys": normalized_deps,
                    },
                )
            if not unresolved_keys:
                continue
            # The planner named something that is not a backlog node: usually
            # a team id, a job, a task label it saw in evidence, or a node it
            # meant to create. Rejecting the whole plan over it used to stall
            # campaigns for dozens of identical cycles. Drop the key, enqueue
            # the task, and tell the planner.
            unresolved.append((item.title, unresolved_keys))
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_DEPENDENCY_DROPPED,
                    "item_id": str(item.id),
                    "title": item.title,
                    "dependency_keys": list(unresolved_keys),
                    "text": (
                        "planner dependency keys matched no backlog item and "
                        "were dropped; the task is enqueued without them"
                    ),
                }
            )
        if unresolved:
            self._planner_dropped_dependency_keys = list(unresolved)
        if state.dropped_parallel:
            committed_titles = {item.title for _task, item in state.pending_items}
            marks: list[tuple[str, str]] = []
            for title, reason in state.dropped_parallel:
                if title in committed_titles and (title, reason) not in marks:
                    marks.append((title, reason))
            if marks:
                self._planner_dropped_parallel_marks = marks
                for title, reason in marks:
                    self._emit(
                        {
                            "type": EventType.LIFE_PLANNER_PARALLEL_DROPPED,
                            "cycle": self._planning_cycles,
                            "title": title,
                            "text": (
                                "the plan marked this task parallel-safe but "
                                "the flag was dropped: " + reason
                            ),
                        }
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
                    supersede_item_ids=[
                        item.id for item in state.revision_active_items
                    ],
                    expected_active_item_ids=(
                        state.revision_witness_active_item_ids or None
                    ),
                    terminalized_source_item_id=(
                        str(revision_request.get("item_id") or "")
                        if state.revision_witness_active_item_ids
                        else ""
                    ),
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
            challenge = revision_request.get("plan_challenge")
            challenge = challenge if isinstance(challenge, dict) else {}
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_COMMITTED,
                    "old_plan_id": expected_plan_id,
                    "old_plan_version": expected_plan_version,
                    "new_plan_id": state.new_plan_id,
                    "new_plan_version": state.new_plan_version,
                    "superseded_item_ids": list(revision_result.superseded_ids),
                    "added_item_ids": list(revision_result.added_ids),
                    "challenge": str(challenge.get("challenge") or ""),
                    "alternative": str(challenge.get("alternative") or ""),
                    "revision_latency_seconds": _challenge_latency(challenge),
                }
            )

        if revision_request is not None and not state.pending_items:
            self._emit(
                {
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "all replacement tasks were filtered",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                }
            )
        return None

    def _pc_retire_tasks(self, state: _PlanCycleState) -> None:
        """Apply retirement even when the cycle returns without enqueueing work."""
        if not state.verdict.retire_tasks:
            return
        if not state.new_plan_id:
            state.new_plan_id = state.expected_plan_id or f"plan-{BacklogItem.new_id()}"
        skipped: list[str] = []
        for item_id, reason in state.verdict.retire_tasks:
            # A park nothing resumes automatically is retirable too: otherwise
            # its dependents wait on it forever and only the operator can act.
            superseded = self.memory.backlog.supersede_items(
                item_ids=(item_id,),
                reason=reason,
                superseded_by_plan_id=state.new_plan_id,
                include_parked=True,
            )
            for retired_id in superseded:
                self._emit({
                    "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                    "item_id": retired_id,
                    "superseded_by_plan_id": state.new_plan_id,
                    "reason": reason,
                    "source": "planner",
                })
            if not superseded:
                skipped.append(item_id)
        if skipped:
            log.info(
                "planner retirement skipped unknown, active, or operator-owned items: %s",
                ", ".join(skipped),
            )

    def _pc_emit_final_verdict(self, state: _PlanCycleState) -> Any:
        verdict = state.verdict
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=PLAN_RETRY,
            cycle=self._planning_cycles,
            project_done=verdict.project_done,
            reason=verdict.summary,
            task_count=len(verdict.new_tasks),
            enqueued_tasks=len(state.added_titles),
            skipped_duplicate_tasks=len(state.skipped_duplicate_titles),
            enqueued_titles=state.added_titles,
            enqueued_impact_scores=state.added_impact_scores,
            skipped_duplicate_titles=state.skipped_duplicate_titles,
            manager_intent=state.manager_intent,
        )
        if not delivered:
            return PLAN_RETRY
        if not state.added_titles:
            filter_feedback = _render_filtered_task_feedback(state)
            if filter_feedback and state.revision_request is None:
                stage = str(self._current_pipeline_stage() or "")
                if not self._persist_manager_planner_feedback(
                    stage=stage,
                    reason=filter_feedback,
                    diagnostic=PLANNER_TASKS_FILTERED_DIAGNOSTIC,
                ):
                    self._emit_status(
                        "failed to persist filtered-task feedback; retry later"
                    )
                    return PLAN_ERROR
            self._enter_idle_backoff()
            if verdict.retire_tasks and not verdict.new_tasks:
                self._emit_status("planner: processed task retirements; retrying after backoff")
            else:
                self._emit_status(
                    "planner: all proposed tasks were filtered; retrying after backoff"
                )
            return PLAN_RETRY
        self._clear_manager_planner_feedback()
        # A queued task is not itself news that the objective moved; the
        # mission that runs it clears the accumulated backoff.
        return True


__all__ = ["PlanningCycleEnqueueMixin"]
