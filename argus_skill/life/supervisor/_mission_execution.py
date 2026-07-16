"""One claimed mission: execute, meter, settle, and persist its outcome."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.stop_kinds import (
    normalize_stop_kind,
    pause_status_for_stop_kind,
    stop_kind_is_recoverable,
)
from ...core.usage import UsageLedger, UsageRecord
from ..mission_outcome import mission_outcome_class
from ..memory import BacklogItem
from ._constants import PLANNER_SCOPE_BOUNDED, PLANNER_SCOPE_FINAL_SUBMISSION
from ._cost import _CostTrackingSink
from ._helpers import _normalize_planner_text

log = logging.getLogger(__name__)


def bounded_dag_node_max_rounds() -> int:
    """Small repair budget for one Planner DAG node.

    A short node should not become a long campaign, but Reviewer ``continue``
    must have somewhere to go. Session rotation is controlled independently by
    ``ARGUS_SKILL_SHIFT_ROUND_LIMIT``; one fresh session per round does not mean
    one round per mission.
    """
    raw = os.environ.get("ARGUS_SKILL_BOUNDED_DAG_NODE_MAX_ROUNDS", "3")
    try:
        return max(2, min(8, int(raw)))
    except ValueError:
        return 3


class MissionExecutionMixin:
    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self.memory.render_prelude()
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        claimed = self.memory.backlog.claim_next()
        if claimed is None or claimed.id != item.id:
            if claimed is not None:
                # Roll back so the next tick sees it again. running →
                # pending is a legal transition (only terminal states
                # are sealed).
                try:
                    self.memory.backlog.update(claimed.id, status="pending")
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: claim rollback failed")
            return {"status": "claim_lost", "item_id": item.id}
        item = claimed
        usage_attempt_id = f"{item.id}:attempt:{max(1, int(item.attempt or 1))}"
        self._missions_started += 1

        self._emit({
            "type": EventType.LIFE_MISSION_STARTED,
            "item_id": item.id,
            "title": item.title,
            # Carry the objective on the event itself (not just the journal
            # entry) so the live mission-context line renders the
            # real goal instead of "objective=-".
            "objective": item.objective,
            "scope": self._planner_scope_from_item(item),
            "missions_started": self._missions_started,
            "attempt": item.attempt,
            "usage_attempt_id": usage_attempt_id,
        })
        # Phase-change callback.
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                self._emit({
                    "type": EventType.LIFE_PHASE_STARTED,
                    "item_id": item.id,
                    "agent_layer": layer,
                    "round_index": info.get("round_index", 0),
                })
            except Exception:  # noqa: BLE001
                log.debug("phase_change event failed; non-critical")

        usage_root = Path(
            getattr(self.memory, "project_root", None)
            or getattr(self.memory, "root", None)
            or self._artifact_root()
        )
        usage_ledger = (
            UsageLedger(usage_root)
            if hasattr(self.runner, "_set_usage_context")
            else None
        )
        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
            usage_ledger=usage_ledger,
            mission_id=usage_attempt_id,
        )

        telemetry_monitor: Any = None
        if self.config.telemetry_dir is not None:
            try:
                from ..telemetry import MissionTelemetryMonitor
                telemetry_monitor = MissionTelemetryMonitor(
                    life_dir=self.config.telemetry_dir,
                    workdir=self._project_workdir(),
                    item_id=item.id,
                    title=item.title,
                    interval_seconds=self.config.telemetry_interval_seconds,
                    stop_event=self.config.stop_event,
                )
                telemetry_monitor.start()
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to start telemetry monitor")

        outcome: Any = None
        exc_str: str | None = None
        t0 = time.time()
        # Per-item codex SESSION ISOLATION (anti context-pollution). The runner
        # chains its codex thread across execute() calls; left unchecked, a brand
        # new, unrelated backlog item RESUMES the previous mission's session and
        # inherits all its context (a plain "你上一个任务干了什么" was resuming a
        # kernel-optimization session and reading its GROUND_TRUTH). A NEW item
        # must start a FRESH session; only iteration cycles of the SAME item keep
        # the thread for continuity. Curated cross-mission memory still flows via
        # the checkpoint/prelude — this only resets the raw thread bleed.
        if getattr(self, "_last_mission_item_id", None) != item.id:
            for _attr in ("_next_seed_thread_id", "last_thread_id"):
                try:
                    if hasattr(self.runner, _attr):
                        setattr(self.runner, _attr, None)
                except Exception:  # noqa: BLE001
                    pass
        self._last_mission_item_id = item.id
        try:
            execute_kwargs: dict[str, Any] = {
                "objective": item.objective,
                "sink": cost_sink,
                "prelude_context": prelude,
                "scope": self._planner_scope_from_item(item),
            }
            original_objective = (
                getattr(item, "original_objective", "") or item.objective
            )
            # F3: a LIVE per-mission budget probe — the engine reads cost_sink each
            # round and hard-stops if the effective cap is reached mid-mission.
            from ._config import MissionBudget
            mission_budget = MissionBudget(
                cap_usd=self._effective_per_mission_cap(item),
                spent=cost_sink.total_usd,
            )
            try:
                from inspect import Parameter, signature

                params = signature(self.runner.execute).parameters
                _accepts_kw = any(
                    p.kind == Parameter.VAR_KEYWORD for p in params.values()
                )
                if "original_objective" in params or _accepts_kw:
                    execute_kwargs["original_objective"] = original_objective
                if "per_mission_budget" in params or _accepts_kw:
                    execute_kwargs["per_mission_budget"] = mission_budget
                if "preplanned" in params or _accepts_kw:
                    execute_kwargs["preplanned"] = any(
                        str(tag).strip().lower() == "planner"
                        for tag in getattr(item, "tags", [])
                    )
                if "mission_id" in params or _accepts_kw:
                    execute_kwargs["mission_id"] = item.id
                if "usage_mission_id" in params or _accepts_kw:
                    execute_kwargs["usage_mission_id"] = usage_attempt_id
                tags = {
                    str(tag).strip().lower()
                    for tag in getattr(item, "tags", [])
                }
                if "bounded_dag_node" in tags:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = (
                            bounded_dag_node_max_rounds()
                        )
            except (TypeError, ValueError):
                execute_kwargs["original_objective"] = original_objective
                execute_kwargs["per_mission_budget"] = mission_budget
                execute_kwargs["mission_id"] = item.id
                execute_kwargs["usage_mission_id"] = usage_attempt_id
            outcome = self.runner.execute(**execute_kwargs)
        except Exception as exc:  # noqa: BLE001
            exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        finally:
            if telemetry_monitor is not None:
                try:
                    telemetry_monitor.stop()
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: failed to stop telemetry monitor")
        elapsed = time.time() - t0

        success = bool(getattr(outcome, "success", False)) if outcome else False
        status = str(getattr(outcome, "status", "error") if outcome else "error")
        rounds = int(getattr(outcome, "rounds", 0) or 0)
        stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        stop_kind = normalize_stop_kind(getattr(outcome, "stop_kind", None))
        if status == "budget_exhausted" and stop_kind is None:
            stop_kind = "budget_exhausted"
        self._evolve_runtime_skills_after_mission(
            success=success,
            usage_mission_id=usage_attempt_id,
            mission_budget=mission_budget,
        )
        usage_summary = cost_sink.usage_summary()
        usd = usage_summary.cost_usd
        known_usd = usage_summary.known_cost_usd
        if usage_ledger is None:
            # Deterministic/memory runners used by tests do not own real
            # ``run_exec`` calls. Persist their aggregate once so subsequent
            # budget checks still exercise the same ledger-only read path.
            UsageLedger(usage_root, migrate_legacy=False).append(
                UsageRecord(
                    call_id=f"memory-mission:{item.id}:{int(t0 * 1_000_000)}",
                    project_id=usage_root.name,
                    mission_id=usage_attempt_id,
                    provider="memory",
                    model="",
                    run_label="memory.mission.aggregate",
                    started_at=t0,
                    completed_at=time.time(),
                    status="completed",
                    input_tokens=usage_summary.input_tokens,
                    cached_input_tokens=usage_summary.cached_input_tokens,
                    output_tokens=usage_summary.output_tokens,
                    reasoning_output_tokens=(
                        usage_summary.reasoning_output_tokens
                    ),
                    premium_requests=usage_summary.premium_requests,
                    pricing_status="priced",
                    pricing_tier="memory_aggregate",
                    cost_usd=known_usd,
                    cost_basis="legacy_aggregate",
                    source="legacy.events",
                )
            )

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop this drain pass so we do not immediately continue
        # with stale credentials, but do not signal the daemon's global
        # stop_event. A 7x24 worker should stay alive so it can recover
        # after credentials are refreshed, and transient provider errors
        # should not kill the supervising process.
        auth_failure = bool(getattr(outcome, "auth_failure", False))
        if auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials if this persists; the daemon "
                    "will keep polling."
                ),
            })

        # The post-mission critic/polish iteration loop was removed (the L1
        # engineer works, the L2 reviewer verifies — no separate critic agent).
        # The ``iteration`` journal/event keys below are kept EMPTY only for
        # schema back-compat. / 事后 critic/迭代循环已移除；下方 journal/event 的
        # ``iteration`` 字段保留为空，仅为 schema 向后兼容。

        # F3: the mid-mission cost breaker fired — PAUSE, do not fail/complete.
        # Roll the item back to PENDING (next tick re-runs from its checkpoint).
        # Anti-cheat: a budget-stopped mission is NEVER marked done/success —
        # the reviewer stays the sole done-ness authority.
        pause_status = pause_status_for_stop_kind(stop_kind)
        if status == "budget_exhausted":
            status = "paused_budget"
            pause_status = status
        if pause_status:
            cap = self._effective_per_mission_cap(item)
            self.memory.backlog.update(
                item.id,
                status=pause_status,
                finished_ts=time.time(),
                last_error=stop_reason,
            )
            self._emit({
                "type": EventType.LIFE_MISSION_COMPLETED,
                "item_id": item.id,
                "success": False,
                "status": pause_status,
                "outcome_class": mission_outcome_class(
                    status=pause_status,
                    success=False,
                ),
                "stop_kind": stop_kind,
                "recoverable": True,
                "cost_usd": usd,
                "known_cost_usd": known_usd,
                "pricing_status": usage_summary.pricing_status,
                "cap_usd": cap,
                "spent_usd": known_usd,
            })
            return {
                "status": pause_status,
                "item_id": item.id,
                "success": False,
                "stop_kind": stop_kind,
                "recoverable": True,
                "cost_usd": usd,
                "known_cost_usd": known_usd,
                "pricing_status": usage_summary.pricing_status,
            }

        stage_transition = getattr(outcome, "stage_transition", {})
        stage_action = (
            str(stage_transition.get("action") or "").strip().lower()
            if isinstance(stage_transition, dict)
            else ""
        )
        normalized_tags = {
            str(tag).strip().lower().replace("-", "_")
            for tag in getattr(item, "tags", [])
        }
        planner_bounded_node = (
            "planner" in normalized_tags
            and self._planner_scope_from_item(item) == PLANNER_SCOPE_BOUNDED
        )
        # ``research_incomplete`` is project-level: it says the persisted final
        # research target is not finished. It must NOT cancel a Manager-certified
        # intermediate stage transition. A scope mission can legitimately end
        # with project-level research still incomplete while the Manager advances
        # ``scope -> solve`` (or rolls back to repair an earlier stage). In that
        # case the same bounded item stays pending and continues automatically.
        # Explicit failures, holds, budget/provider pauses, and infrastructure
        # blocks do not enter this path.
        project_incomplete_but_stage_progressed = (
            status == "research_incomplete"
            and stage_action in {"advance", "rollback"}
        )
        staged_item_continues = (
            (success or project_incomplete_but_stage_progressed)
            and not self.config.continuous
            and not planner_bounded_node
            and isinstance(stage_transition, dict)
            and bool(stage_transition)
            and stage_action != "complete"
        )
        if staged_item_continues:
            self.memory.backlog.update(
                item.id,
                status="pending",
                started_ts=None,
                finished_ts=None,
                last_error="",
            )
            held = stage_action not in {"advance", "rollback"}
            if held:
                self._update_no_progress_streak(
                    kind="mission_failed",
                    report={
                        "forward_progress": False,
                        "headline": f"manager stage decision: {stage_action or 'unknown'}",
                    },
                )
            return {
                "success": True,
                "status": "stage_hold" if held else "stage_continues",
                "item_id": item.id,
                "stage_transition": stage_transition,
                "cost_usd": usd,
                "known_cost_usd": known_usd,
                "pricing_status": usage_summary.pricing_status,
            }

        # Planner-authored bounded DAG nodes are separate acceptance units: once
        # the Manager has certified ``advance`` for the current project stage,
        # that node is complete even if the Reviewer described the WHOLE project
        # as ``research_incomplete``. Close the node so its solve/review dependent
        # can unlock. A HOLD remains incomplete; a ROLLBACK is not silently
        # treated as node success.
        planner_node_stage_completed = (
            planner_bounded_node
            and status == "research_incomplete"
            and stage_action == "advance"
        )
        if planner_node_stage_completed:
            success = True
            status = "done"
            stop_reason = ""

        research_pause = status in {
            "research_incomplete",
            "paused_no_breakthrough",
            "exhausted_current_methods",
            "infra_blocked",
        }
        replan_requested = status == "replan_requested"
        err = exc_str or stop_reason or "unspecified failure"

        # Update backlog row. A bounded research cycle that did not achieve its
        # persisted success target is resumable, not a success or terminal failure.
        if success:
            self.memory.backlog.mark_done(item.id)
        elif replan_requested:
            self.memory.backlog.update(
                item.id,
                status="pending",
                started_ts=None,
                finished_ts=None,
                last_error=stop_reason,
            )
        elif research_pause:
            self.memory.backlog.update(
                item.id,
                status=status,
                finished_ts=time.time(),
                last_error=stop_reason,
            )
        else:
            self.memory.backlog.mark_failed(item.id, error=err)

        # A "blocked" verdict means the REVIEWER stopped progress because it
        # needs the OPERATOR to make a call — not a bug/crash. Persist the
        # question onto the (now-terminal) item so it outlives this one event:
        # /status can list every currently-unanswered question across ALL
        # projects/restarts, not just whatever a cockpit happened to be tailing
        # live when it was asked (the old process-local state
        # ``blocked_question``, which was lost the moment that process exited).
        # Writing a non-status field onto an already-terminal item is legal —
        # Backlog.update()'s IllegalStateTransition seal only guards STATUS
        # transitions, not other fields.
        if status == "blocked":
            operator_question = str(
                getattr(outcome, "operator_question", "") or ""
            ).strip()
            if operator_question:
                try:
                    self.memory.backlog.update(
                        item.id, pending_question=operator_question,
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "life supervisor: failed to persist pending_question"
                    )

        kind = (
            "mission_complete"
            if success
            else "mission_replan_requested"
            if replan_requested
            else "mission_failed"
        )
        final_submission_certified = bool(
            kind == "mission_complete"
            and self._planner_scope_from_item(item) == PLANNER_SCOPE_FINAL_SUBMISSION
            and getattr(outcome, "final_submission_certified", False)
        )
        planner_report = (
            getattr(outcome, "planner_report", {})
            if isinstance(getattr(outcome, "planner_report", {}), dict)
            else {}
        )
        checklist_feedback = (
            getattr(outcome, "checklist_feedback", {})
            if isinstance(getattr(outcome, "checklist_feedback", {}), dict)
            else {}
        )
        step_back = (
            getattr(outcome, "step_back", None)
            if isinstance(getattr(outcome, "step_back", None), dict)
            else None
        )
        research_result = (
            dict(getattr(outcome, "research_result", {}))
            if isinstance(getattr(outcome, "research_result", {}), dict)
            else {}
        )
        completion_summary = self._completion_evidence_from_outcome(outcome)
        if final_submission_certified:
            self._persist_final_submission_certification(title=item.title)

        self._update_no_progress_streak(
            kind=kind, report=getattr(outcome, "planner_report", {})
        )

        scientist_totals = cost_sink.scientist_totals()
        scientist_usage_by_model = cost_sink.scientist_usage_by_model_snapshot()
        self._emit({
            "type": EventType.LIFE_MISSION_COMPLETED,
            "item_id": item.id,
            "title": item.title,
            "objective": item.objective,
            "scope": self._planner_scope_from_item(item),
            "success": success,
            "status": status,
            "outcome_class": mission_outcome_class(status=status, success=success),
            "rounds": rounds,
            "elapsed_seconds": elapsed,
            "cost_usd": usd,
            "known_cost_usd": known_usd,
            "pricing_status": usage_summary.pricing_status,
            "usage_record_count": usage_summary.call_count,
            "partial_usage_records": usage_summary.partial_calls,
            "unpriced_usage_records": usage_summary.unpriced_calls,
            "planner_task_signature": {
                "title": _normalize_planner_text(item.title),
                "objective": _normalize_planner_text(item.objective),
            }
            if kind == "mission_failed"
            else {},
            "terminal_status": status if kind == "mission_failed" else "",
            "resumable": research_pause or stop_kind_is_recoverable(stop_kind),
            "recoverable": bool(
                getattr(outcome, "recoverable", False)
                or stop_kind_is_recoverable(stop_kind)
            ),
            "stop_kind": stop_kind,
            "stop_reason": (stop_reason or err) if kind == "mission_failed" else "",
            "failure_reason": err if kind == "mission_failed" else "",
            "agent_layer": "engineer",
            "engineer_model": self.engineer_model,
            "reviewer_model": self.reviewer_model,
            "scientist_cost_usd": cost_sink.scientist_usd(),
            "engineer_cost_usd": cost_sink.engineer_usd(),
            "reviewer_cost_usd": cost_sink.reviewer_usd(),
            # util (manager/classify) + copilot premium-request cost were folded
            # into total_usd() but never surfaced in the breakdown — emit them so
            # the cost is fully auditable. copilot_premium_requests is the raw
            # count (GitHub bills per premium request, flat $/req — NOT per token,
            # so a copilot mission's whole dollar cost is this count * rate).
            "util_cost_usd": cost_sink.util_usd(),
            "copilot_cost_usd": cost_sink.copilot_usd(),
            "copilot_premium_requests": cost_sink.copilot_premium_request_total(),
            "scientist_input_tokens": scientist_totals[0],
            "scientist_cached_input_tokens": scientist_totals[1],
            "scientist_output_tokens": scientist_totals[2],
            "scientist_reasoning_output_tokens": scientist_totals[3],
            "scientist_usage_by_model": {
                model: {
                    "input_tokens": values[0],
                    "cached_input_tokens": values[1],
                    "output_tokens": values[2],
                    "reasoning_output_tokens": values[3],
                }
                for model, values in scientist_usage_by_model.items()
            },
            "input_tokens": cost_sink.total_input_tokens(),
            "cached_input_tokens": cost_sink.total_cached_input_tokens(),
            "cache_write_tokens": cost_sink.total_cache_write_tokens(),
            "output_tokens": cost_sink.total_output_tokens(),
            "reasoning_output_tokens": cost_sink.total_reasoning_output_tokens(),
            "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
            "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
            "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
            "completion_summary": completion_summary,
            "research_result": research_result or None,
            "planner_report": planner_report,
            "checklist_feedback": checklist_feedback,
            "step_back": step_back,
            "final_submission_certified": final_submission_certified,
            "iteration": None,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "known_cost_usd": known_usd,
            "pricing_status": usage_summary.pricing_status,
            "iteration": None,
            "auth_failure": auth_failure,
            "planner_report": planner_report,
            "expected_plan_id": item.plan_id,
            "expected_plan_version": item.plan_version,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------


__all__ = ["MissionExecutionMixin"]
