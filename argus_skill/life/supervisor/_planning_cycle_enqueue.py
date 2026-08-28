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
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ..memory import BacklogItem
from ._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
    PLANNER_DEDUP_STATUSES,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    REPLAN_FILTER_REJECTION_LIMIT,
)
from ._helpers import (
    _entry_task_signature,
    _normalize_blocker_fingerprint,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
    _unique_normalized_task_key_aliases,
)
from ._planner_rendering import _forward_progress
from ._planning_cycle_helpers import _PlanCycleState, _revision_reason

log = logging.getLogger(__name__)


def _latest_planner_forward_progress(
    memory: Any,
    revision_request: dict[str, Any] | None,
) -> bool:
    """Whether the latest settled mission explicitly advanced the objective."""
    if isinstance(revision_request, dict):
        report = revision_request.get("planner_report")
        value = report.get("forward_progress") if isinstance(report, dict) else None
        if isinstance(value, bool):
            return value
    try:
        entries = memory.journal.tail(32)
    except Exception:  # noqa: BLE001 - backoff remains conservative on read failure
        return False
    for entry in reversed(entries):
        if str(getattr(entry, "kind", "") or "") not in {
            "mission_complete",
            "mission_failed",
            "mission_replan_requested",
        }:
            continue
        return _forward_progress(entry) is True
    return False


def _independent_review_forced() -> bool:
    return os.environ.get(
        "ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW", ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _stage_closing_forced() -> bool:
    return os.environ.get(
        "ARGUS_SKILL_FORCE_STAGE_CLOSING", ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _research_stage_ready_for_close(
    *,
    state_root: Path,
    evidence_root: Path,
) -> bool:
    """Promote the next single task when deterministic research blockers are gone."""
    try:
        from ...core.pipeline_state import read_pipeline_state
        from ...verticals._base import (
            load_vertical,
            vertical_stage_completion_issues,
        )

        pipeline = read_pipeline_state(state_root)
        if not isinstance(pipeline, dict):
            return False
        if (
            str(pipeline.get("vertical") or "").strip() != "research"
            or str(pipeline.get("current_stage") or "").strip() != "research"
        ):
            return False
        selection = evidence_root / "research" / "IDEA_SELECTION.json"
        positioning = evidence_root / "paper" / "novelty_audit.md"
        grounding = evidence_root / "research" / "LITERATURE_GROUNDING.json"
        if not selection.is_file() or not (
            positioning.is_file() or grounding.is_file()
        ):
            return False
        definition = load_vertical("research", project_root=state_root)
        return not vertical_stage_completion_issues(
            definition,
            stage="research",
            project_root=evidence_root,
        )
    except Exception:  # noqa: BLE001 - automatic closing is fail-open to normal planning
        return False


def _apply_planner_stage_request(
    *,
    state_root: Path,
    requested_stage: str,
    reason: str,
    evidence_root: Path,
) -> None:
    """Apply a Manager-owned Planner stage request in either valid direction."""
    from ...skills.stage_machine import (
        advance_stage,
        current_stage,
        rollback_stage,
    )

    if requested_stage == current_stage(state_root):
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
        except ValueError:
            raise advance_error


class PlanningCycleEnqueueMixin:
    """Dedupe index, pending-item construction, commit, and final emission."""

    @staticmethod
    def _terminal_blocker_is_dedupable(item: BacklogItem) -> bool:
        """Return whether an unchanged failed task is known to be unrecoverable."""
        outcome = item.outcome if isinstance(item.outcome, dict) else {}
        return bool(
            item.status == "failed"
            and not item.pending_question
            and outcome.get("execution_status") == "blocked"
            and outcome.get("review_status") == "blocked"
            and outcome.get("resumable") is False
        )

    def _pc_build_dedupe_index(self, state: _PlanCycleState) -> Any | None:
        try:
            # Planner semantic dedup intentionally includes archived terminal
            # node ids/keys; otherwise compaction would repurchase old work.
            state.existing_items = self.memory.backlog.history()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            state.existing_items = []

        seen_signatures: dict[tuple[str, ...], BacklogItem] = {}
        active_base_signatures: dict[tuple[str, ...], BacklogItem] = {}
        active_node_keys: dict[str, BacklogItem] = {}
        terminal_blocker_fingerprints: dict[str, BacklogItem] = {}
        revision_active_ids = {item.id for item in state.revision_active_items}
        for existing in state.existing_items:
            if existing.id in revision_active_ids:
                continue
            terminal_blocker = self._terminal_blocker_is_dedupable(existing)
            if (
                existing.status not in PLANNER_DEDUP_STATUSES
                and not terminal_blocker
            ):
                continue
            if terminal_blocker:
                terminal_blocker_fingerprints.setdefault(
                    f"item:{existing.id.lower()}",
                    existing,
                )
                blocker_fingerprint = _normalize_blocker_fingerprint(
                    existing.blocker_fingerprint
                )
                if blocker_fingerprint:
                    terminal_blocker_fingerprints.setdefault(
                        blocker_fingerprint,
                        existing,
                    )
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
            base_signature = signature
            if existing.status != "done" and not terminal_blocker:
                active_base_signatures[base_signature] = existing
                seen_signatures[signature] = existing
                node_key = str(existing.node_key or "").strip()
                if node_key:
                    active_node_keys[node_key] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        state.seen_signatures = seen_signatures
        state.active_base_signatures = active_base_signatures
        state.active_node_keys = active_node_keys
        state.terminal_blocker_fingerprints = terminal_blocker_fingerprints
        state.recent_failures = self._recent_no_progress_failures()
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

    def _stage_closing_reproposal_blocker(
        self, task: Any,
    ) -> tuple[Any, str, float] | None:
        """Reject certification churn until substantive repair intervenes.

        A completed independently reviewed stage-closing mission is one review
        attempt.  If the Manager kept the same stage open, immediately asking a
        fresh Engineer and Reviewer to package the same gate again cannot change
        that decision.  The next accepted unit must be a non-stage-closing repair
        on the same stage; after such a repair, one new certification attempt is
        legal.

        Older backlog rows have no ``stage:<name>`` tag and intentionally keep
        their historical behaviour.  This makes the guard migration-safe.
        """
        if not bool(getattr(task, "stage_closing", False)):
            return None
        if bool(getattr(task, "stage_repair", False)):
            return None
        stage_reader = getattr(self, "_current_pipeline_stage", None)
        if not callable(stage_reader):
            return None
        try:
            current_stage = str(stage_reader() or "").strip().lower()
            rows = list(self.memory.backlog.history())
        except Exception:  # noqa: BLE001 - dedupe remains fail-open
            return None
        if not current_stage:
            return None

        def finished_at(item: Any) -> float:
            return float(
                getattr(item, "finished_ts", 0.0)
                or getattr(item, "started_ts", 0.0)
                or getattr(item, "ts", 0.0)
                or 0.0
            )

        latest = None
        cutoff = 0.0
        try:
            from ...core.stage_certificate import latest_stage_review

            certificate = latest_stage_review(self.memory.root, current_stage)
        except Exception:  # noqa: BLE001 - backlog fallback remains available
            certificate = None
        if certificate and certificate.get("review_status") == "done":
            task_id = str(certificate.get("task_id") or "")
            latest = next((item for item in rows if item.id == task_id), None)
            if latest is None:
                latest = SimpleNamespace(id=task_id or "stage-review", status="done")
            cutoff = float(certificate.get("recorded_at") or 0.0)
        else:
            reviewed: list[Any] = []
            for item in rows:
                if self._item_pipeline_stage(item) != current_stage:
                    continue
                if not self._item_is_stage_closing(item) or item.status != "done":
                    continue
                outcome = getattr(item, "outcome", {}) or {}
                if str(outcome.get("review_status") or "").strip().lower() != "done":
                    continue
                reviewed.append(item)
            if not reviewed:
                return None
            latest = max(reviewed, key=finished_at)
            cutoff = finished_at(latest)

        # A successful ordinary mission after the review is the evidence delta
        # that unlocks one new stage-closing attempt.  Merely renaming or
        # repackaging another certification does not.
        for item in rows:
            if self._item_pipeline_stage(item) != current_stage:
                continue
            if self._item_is_stage_closing(item) or item.status != "done":
                continue
            if finished_at(item) > cutoff:
                return None

        reason = (
            f"stage {current_stage!r} already has a completed independent "
            f"certification attempt ({latest.id}); run a non-stage-closing "
            "repair that changes the stage evidence before requesting another "
            "certification"
        )
        return latest, reason, cutoff

    def _gate_reproposal_is_not_a_duplicate(self, task: Any, duplicate_item: Any) -> bool:
        """Whether a stage-closing proposal escapes the duplicate filter.

        Review semantics are part of task identity. A prior ordinary task
        cannot satisfy a later stage-closing
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
        have completed. The stage-level guard above now additionally requires an
        intervening non-stage-closing repair before this signature exemption can
        be reached. A still-pending or running duplicate is a genuine duplicate
        and is still filtered, so concurrent copies of in-flight work remain
        impossible.
        """
        stage_closing = bool(getattr(task, "stage_closing", False))
        requires_review = stage_closing or bool(
            getattr(task, "require_independent_review", True)
        )
        if not requires_review:
            return False
        if stage_closing and duplicate_item.status == "done":
            return True
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
                    reason=state.verdict.reason or "Planner requested stage transition",
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
        auto_close_research = (
            _research_stage_ready_for_close(
                state_root=Path(state_root),
                evidence_root=Path(context_root).resolve(),
            )
        )
        if auto_close_research:
            try:
                from ...skills.stage_machine import advance_stage

                advance_stage(
                    state_root,
                    target_stage="plan",
                    reason="selected research target and positioning are complete",
                    advanced_by="manager:auto_completion",
                    evidence_root=Path(context_root).resolve(),
                )
                # The tasks were authored under the old research-stage context.
                # Replan immediately so they cannot become stale closeout work in plan.
                return PLAN_RETRY
            except Exception:  # noqa: BLE001 - normal Manager planning remains available
                log.debug("automatic research stage advance failed", exc_info=True)
        for task_index, task in enumerate(planned_tasks):
            task = replace(task, context_refs=[], execution_workdir="")
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
                str(path).strip().replace("\\", "/").strip("/")
                for path in (getattr(task, "owns_paths", []) or [])
                if (
                    str(path).strip()
                    and not Path(str(path)).is_absolute()
                    and Path(str(path)).parts
                    and ".." not in Path(str(path)).parts
                )
            ]
            canonical_stage_closing = bool(
                canonical_scope == PLANNER_SCOPE_FINAL_SUBMISSION
                or getattr(task, "stage_repair", False)
                or _stage_closing_forced()
                or host_stage_closing
            )
            canonical_require_review = bool(
                canonical_stage_closing
                or _independent_review_forced()
                or getattr(task, "require_independent_review", True)
            )
            task = replace(
                task,
                scope=canonical_scope,
                acceptance_check=canonical_acceptance,
                context_refs=canonical_context_refs,
                stage_closing=canonical_stage_closing,
                blocker_fingerprint=_normalize_blocker_fingerprint(
                    getattr(task, "blocker_fingerprint", "")
                ),
                require_independent_review=canonical_require_review,
                skip_stage_transition=False,
                allow_skill_changes=False,
                parallel_safe=bool(
                    getattr(task, "parallel_safe", False)
                    and canonical_owns_paths
                    and not canonical_stage_closing
                ),
                owns_paths=canonical_owns_paths,
            )
            from ...skills.stage_machine import current_stage
            from ...skills.vertical_select import resolve_vertical
            from ...verticals._base import load_vertical_contract

            policy_root = Path(context_root or self._project_workdir()).resolve()
            policy_stage = current_stage(state_root)
            campaign_vertical = resolve_vertical(state_root)
            policy_vertical = (
                str(getattr(task, "vertical", "") or "").strip()
                or campaign_vertical
            )
            try:
                policy_contract = load_vertical_contract(
                    policy_vertical,
                    project_root=state_root,
                )
            except LookupError:
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "skip_category": "unknown_task_vertical",
                    "reason": f"unknown Planner task vertical: {policy_vertical}",
                })
                continue
            policy_issues = policy_contract.planner_task_issues(
                policy_stage,
                policy_root,
                task,
            )
            if policy_issues:
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "skip_category": "vertical_task_policy",
                    "reason": "; ".join(policy_issues),
                })
                continue

            certification_blocker = self._stage_closing_reproposal_blocker(task)
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
            base_signature = signature
            terminal_duplicate = None
            if task.blocker_fingerprint:
                terminal_duplicate = state.terminal_blocker_fingerprints.get(
                    task.blocker_fingerprint
                )
            planner_node_key = str(getattr(task, "key", "") or "").strip()
            node_key_duplicate = (
                state.active_node_keys.get(planner_node_key)
                if planner_node_key
                else None
            )
            duplicate_item = (
                node_key_duplicate
                or terminal_duplicate
                or state.active_base_signatures.get(
                    base_signature
                )
                or state.seen_signatures.get(signature)
            )
            terminal_fingerprint_match = terminal_duplicate is not None
            review_purchase = policy_contract.review_purchase(
                project_root=policy_root,
                task=task,
                existing_items=state.existing_items,
                semantic_duplicate=duplicate_item,
                stage_reviewed_at=(
                    certification_blocker[2]
                    if certification_blocker is not None
                    else None
                ),
            )
            if certification_blocker is not None and not (
                review_purchase is not None
                and review_purchase.release_stage_closing_blocker
            ):
                prior_item, blocker_reason, _reviewed_at = certification_blocker
                state.skipped_certification_reproposal_titles.append(task.title)
                state.skipped_certification_reproposal_reasons.append(blocker_reason)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                        "cycle": self._planning_cycles,
                        "title": task.title,
                        "objective": task.objective,
                        "impact_score": task.impact_score,
                        "impact_area": task.impact_area,
                        "evidence": task.evidence,
                        "matched_item_id": prior_item.id,
                        "matched_status": prior_item.status,
                        "matched_stage": self._item_pipeline_stage(prior_item),
                        "skip_category": (
                            "stage_closing_requires_intervening_repair"
                        ),
                        "reason": blocker_reason,
                    }
                )
                continue
            if (
                review_purchase is not None
                and review_purchase.discard_semantic_duplicate
            ):
                duplicate_item = None
            review_purchase_reason = (
                review_purchase.defer_reason if review_purchase is not None else ""
            )
            if review_purchase_reason:
                state.skipped_certification_reproposal_titles.append(task.title)
                state.skipped_certification_reproposal_reasons.append(
                    review_purchase_reason
                )
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "skip_category": "paper_review_purchase_deferred",
                    "reason": review_purchase_reason,
                })
                continue
            if (
                duplicate_item is not None
                and node_key_duplicate is None
                and not terminal_fingerprint_match
                and self._gate_reproposal_is_not_a_duplicate(task, duplicate_item)
            ):
                duplicate_item = None
            if duplicate_item is not None:
                if getattr(task, "key", ""):
                    key_map[task.key] = duplicate_item.id
                state.skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate terminal blocker"
                    if self._terminal_blocker_is_dedupable(duplicate_item)
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
            recent_failure = state.recent_failures.get(signature[:2])
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
                    "reason": str(state.verdict.reason or "Planner waiver"),
                })
            item = BacklogItem.new(
                item_id=item_id,
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=task_tags,
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                plan_id=state.new_plan_id,
                plan_version=state.new_plan_version,
                node_key=str(getattr(task, "key", "") or item_id),
                context_refs=task_context_refs,
                blocker_fingerprint=str(
                    getattr(task, "blocker_fingerprint", "") or ""
                ),
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
                non_goals=list(getattr(task, "non_goals", []) or []),
                original_objective=str(
                    getattr(self.config, "continuous_objective", "") or ""
                ),
                authorization_id=authorization_id,
                authorization_action=authorization_action,
                manager_decision=manager_decision,
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

    def _pc_record_revision_rejection(
        self,
        state: _PlanCycleState,
        *,
        reason: str,
        nonterminal_result: Any | None,
    ) -> Any | None:
        revision_request = state.revision_request or {}
        requested_item_id = str(revision_request.get("item_id") or "")
        requested_item = next(
            (
                item
                for item in state.revision_active_items
                if item.id == requested_item_id
            ),
            None,
        )
        attempts = int(getattr(requested_item, "replan_rejections", 0) or 0) + 1
        if requested_item is not None:
            self.memory.backlog.update(
                requested_item.id,
                replan_rejections=attempts,
                last_error=f"{reason} (attempt {attempts})",
            )
        terminal = attempts >= REPLAN_FILTER_REJECTION_LIMIT
        self._emit(
            {
                "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                "reason": reason,
                "expected_plan_id": state.expected_plan_id,
                "expected_plan_version": state.expected_plan_version,
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
                "planner replacement remained invalid after bounded retries; "
                "current node failed closed and awaits new evidence"
            )
            self._suggested_sleep_s = max(self._suggested_sleep_s, sleep_s)
            return PLAN_TERMINAL_IDLE
        return nonterminal_result

    def _pc_commit_pending_items(self, state: _PlanCycleState) -> Any | None:
        revision_request = state.revision_request
        expected_plan_id = state.expected_plan_id
        expected_plan_version = state.expected_plan_version
        manager_intent = state.manager_intent

        # Pass 2: resolve dep keys to real item ids, then enqueue. A later
        # planning cycle may naturally depend on a prior node, so include stable
        # node keys already persisted in the backlog. Unknown keys still reject
        # the whole batch; executing a child without its required parent is unsafe.
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
            if task_deps:
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
                if unresolved_keys:
                    unresolved.append((item.title, unresolved_keys))
        if unresolved:
            details = "; ".join(
                f"{title!r}: {keys}" for title, keys in unresolved
            )
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_ERROR,
                    "cycle": self._planning_cycles,
                    "error": f"planner DAG has unresolved dependencies: {details}",
                }
            )
            self._emit_status(
                "planner DAG rejected because dependencies became unresolved"
            )
            self._enter_idle_backoff()
            if revision_request is not None:
                return self._pc_record_revision_rejection(
                    state,
                    reason=(
                        "replacement DAG rejected because dependencies became "
                        f"unresolved: {details}"
                    ),
                    nonterminal_result=PLAN_ERROR,
                )
            return PLAN_ERROR
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
                    "manager_action": str(
                        challenge.get("manager_action") or "revise"
                    ),
                    "challenge": str(challenge.get("challenge") or ""),
                    "alternative": str(challenge.get("alternative") or ""),
                    "revision_latency_seconds": float(
                        challenge.get("revision_latency_seconds") or 0.0
                    ),
                }
            )

        if revision_request is not None and not state.pending_items:
            return self._pc_record_revision_rejection(
                state,
                reason="all replacement tasks were filtered",
                nonterminal_result=None,
            )
        return None

    def _pc_emit_final_verdict(self, state: _PlanCycleState) -> Any:
        verdict = state.verdict
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=PLAN_RETRY,
            cycle=self._planning_cycles,
            project_done=verdict.project_done,
            reason=verdict.reason,
            task_count=len(verdict.new_tasks),
            enqueued_tasks=len(state.added_titles),
            skipped_duplicate_tasks=len(state.skipped_duplicate_titles),
            skipped_certification_reproposal_tasks=len(
                state.skipped_certification_reproposal_titles
            ),
            skipped_recent_failure_tasks=len(state.skipped_recent_failure_titles),
            skipped_subagent_family_failure_tasks=len(state.skipped_subagent_family_failure_titles),
            enqueued_titles=state.added_titles,
            enqueued_impact_scores=state.added_impact_scores,
            skipped_duplicate_titles=state.skipped_duplicate_titles,
            skipped_certification_reproposal_titles=(
                state.skipped_certification_reproposal_titles
            ),
            skipped_certification_reproposal_reasons=(
                state.skipped_certification_reproposal_reasons
            ),
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
            if state.skipped_certification_reproposal_reasons:
                self._emit_status(
                    "planner: repeated stage certification rejected; a substantive "
                    "same-stage repair must complete before recertification"
                )
            else:
                self._emit_status(
                    "planner: all proposed tasks were filtered; retrying after backoff"
                )
            return PLAN_RETRY
        self._clear_manager_planner_feedback()
        # A queued task is not itself evidence that the objective moved. Preserve
        # the accumulated backoff across hollow/repeated planning cycles; only
        # the Reviewer's explicit objective-level signal clears it.
        if _latest_planner_forward_progress(self.memory, state.revision_request):
            self._reset_idle_backoff()
        return True


__all__ = ["PlanningCycleEnqueueMixin", "_latest_planner_forward_progress"]
