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
from ..memory import BacklogItem
from ..mission_outcome import mission_outcome_class, mission_outcome_dimensions
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


def is_progressive_experiment_matrix(item: BacklogItem) -> bool:
    """Return whether a task is a progress-bearing experiment matrix."""
    tags = {
        str(tag).strip().lower()
        for tag in getattr(item, "tags", [])
    }
    if "experiment_matrix" in tags:
        return True
    text = f"{item.title}\n{item.objective}".lower()
    return "matrix" in text and any(
        marker in text
        for marker in (
            "experiment",
            "evaluation",
            "benchmark",
            "canonical",
            "run-stage",
            "e0",
        )
    )


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
        pipeline_stage_at_start = self._current_pipeline_stage() or ""
        usage_attempt_id = f"{item.id}:attempt:{max(1, int(item.attempt or 1))}"
        self._missions_started += 1
        item_scope = self._planner_scope_from_item(item)

        self._emit({
            "type": EventType.LIFE_MISSION_STARTED,
            "item_id": item.id,
            "title": item.title,
            # Carry the objective on the event itself (not just the journal
            # entry) so the live mission-context line renders the
            # real goal instead of "objective=-".
            "objective": item.objective,
            "scope": item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
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
        context_packet_path: Path | None = None
        try:
            from ..context_packet import create_mission_context

            context_packet_path = create_mission_context(
                life_dir=usage_root,
                mission_id=item.id,
                stage=pipeline_stage_at_start,
                scope=item_scope,
                objective=item.objective,
                acceptance_check=getattr(item, "acceptance_check", ""),
                non_goals=list(getattr(item, "non_goals", []) or []),
                context_refs=list(getattr(item, "context_refs", []) or []),
                plan_id=item.plan_id,
                plan_version=item.plan_version,
                node_key=item.node_key,
                deps=item.deps,
                tags=item.tags,
            )
        except Exception:  # noqa: BLE001 - packet persistence must fail soft
            log.exception("life supervisor: failed to create mission context packet")
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
        repair_store: Any = None
        repair_identity: Any = None
        repair_capability: dict[str, Any] | None = None
        recovered_repair_settlement: dict[str, Any] | None = None
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
                "scope": item_scope,
            }
            original_objective = (
                getattr(item, "original_objective", "") or item.objective
            )
            authorization_id = str(
                getattr(item, "authorization_id", "") or ""
            ).strip()
            authorization_action = str(
                getattr(item, "authorization_action", "") or ""
            ).strip().lower()
            if bool(authorization_id) != bool(authorization_action):
                raise ValueError("backlog authorization reference is incomplete")
            if authorization_id:
                if authorization_action != "validator_repair":
                    raise ValueError("unsupported authorized mission action")
                from ...manager.control_state import CampaignControlStore

                repair_store = CampaignControlStore(
                    Path(self.memory.root),
                    project_root=self._project_workdir(),
                )
                existing = repair_store.current_repair_capability(
                    mission_id=item.id,
                )
                if existing is not None:
                    if (
                        existing.get("authorization_id") != authorization_id
                        or existing.get("action") != authorization_action
                    ):
                        raise ValueError("running repair capability does not match backlog")
                    repair_identity = repair_store.campaign_identity(
                        campaign_epoch=int(existing.get("campaign_epoch") or 0),
                    )
                    repair_capability = existing
                    if existing.get("event") == "closed":
                        recovered_repair_settlement = existing
                else:
                    authorization = repair_store.get_authorization(authorization_id)
                    if authorization is None:
                        raise ValueError("Manager authorization is unavailable")
                    repair_identity = repair_store.campaign_identity(
                        campaign_epoch=int(authorization.get("campaign_epoch") or 0),
                    )
                    claimed = repair_store.claim_repair_capability(
                        authorization_id=authorization_id,
                        nonce=str(authorization.get("nonce") or ""),
                        action=authorization_action,
                        identity=repair_identity,
                        mission_id=item.id,
                    )
                    repair_capability = {
                        name: getattr(claimed, name)
                        for name in claimed.__dataclass_fields__
                    }
                if repair_capability.get("status") == "claimed":
                    started = repair_store.begin_acceptance_retry(
                        capability_id=str(repair_capability["capability_id"]),
                        nonce=str(repair_capability["nonce"]),
                        identity=repair_identity,
                    )
                    repair_capability = {
                        name: getattr(started, name)
                        for name in started.__dataclass_fields__
                    }
                public_repair = (
                    "## Restricted validator repair capability\n"
                    f"- authorization_id: {authorization_id}\n"
                    f"- capability_id: {repair_capability['capability_id']}\n"
                    f"- validator_id: {repair_capability['validator_id']}\n"
                    "- allowed_write_paths: "
                    + ", ".join(repair_capability.get("allowed_write_paths") or [])
                    + "\n- scientific evidence, preregistration, thresholds, and "
                    "success criteria are frozen. Edit only the listed paths. "
                    "Run the same acceptance checks once. Reviewer must compare "
                    "the old and new validator logic and reject any lowered "
                    "scientific standard."
                )
                execute_kwargs["prelude_context"] = (
                    public_repair + "\n\n---\n" + prelude
                    if prelude else public_repair
                )
            try:
                from inspect import Parameter, signature

                params = signature(self.runner.execute).parameters
                _accepts_kw = any(
                    p.kind == Parameter.VAR_KEYWORD for p in params.values()
                )
                if "original_objective" in params or _accepts_kw:
                    execute_kwargs["original_objective"] = original_objective
                if "preplanned" in params or _accepts_kw:
                    execute_kwargs["preplanned"] = any(
                        str(tag).strip().lower() == "planner"
                        for tag in getattr(item, "tags", [])
                    )
                if "require_independent_review" in params or _accepts_kw:
                    execute_kwargs["require_independent_review"] = (
                        self._item_requires_independent_review(item)
                    )
                if "mission_id" in params or _accepts_kw:
                    execute_kwargs["mission_id"] = item.id
                if "usage_mission_id" in params or _accepts_kw:
                    execute_kwargs["usage_mission_id"] = usage_attempt_id
                if "context_packet_path" in params or _accepts_kw:
                    execute_kwargs["context_packet_path"] = (
                        str(context_packet_path) if context_packet_path else ""
                    )
                tags = {
                    str(tag).strip().lower()
                    for tag in getattr(item, "tags", [])
                }
                progressive_matrix = is_progressive_experiment_matrix(item)
                if (
                    "progressive_experiment_matrix" in params
                    or _accepts_kw
                ):
                    execute_kwargs["progressive_experiment_matrix"] = (
                        progressive_matrix
                    )
                if "bounded_dag_node" in tags and not progressive_matrix:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = (
                            bounded_dag_node_max_rounds()
                        )
                if repair_capability is not None:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = 1
                    if "workflow_mode_override" in params or _accepts_kw:
                        execute_kwargs["workflow_mode_override"] = "direct"
            except (TypeError, ValueError):
                execute_kwargs["original_objective"] = original_objective
                execute_kwargs["mission_id"] = item.id
                execute_kwargs["usage_mission_id"] = usage_attempt_id
                execute_kwargs["require_independent_review"] = (
                    self._item_requires_independent_review(item)
                )
                execute_kwargs["context_packet_path"] = (
                    str(context_packet_path) if context_packet_path else ""
                )
                if repair_capability is not None:
                    execute_kwargs["max_rounds_override"] = 1
                    execute_kwargs["workflow_mode_override"] = "direct"
            if recovered_repair_settlement is not None:
                from types import SimpleNamespace

                recovered_accepted = bool(
                    recovered_repair_settlement.get("accepted")
                )
                outcome = SimpleNamespace(
                    success=recovered_accepted,
                    status="done" if recovered_accepted else "error",
                    stop_reason=str(
                        recovered_repair_settlement.get("reason") or ""
                    ),
                    rounds=0,
                    final_review_status=(
                        "done" if recovered_accepted else "not_assessed"
                    ),
                    failure_source="",
                    stage_transition={},
                )
            else:
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

        pause_status = pause_status_for_stop_kind(stop_kind)
        if status == "budget_exhausted":
            status = "paused_budget"
            pause_status = status
        if pause_status:
            pause_outcome = mission_outcome_dimensions(
                status=pause_status,
                success=False,
                review_status=str(
                    getattr(outcome, "final_review_status", "") or ""
                ),
                scientific_decision=str(
                    getattr(outcome, "scientific_decision", "") or ""
                ),
                failure_source=str(
                    getattr(outcome, "failure_source", "") or ""
                ),
                stop_kind=stop_kind,
                resumable=True,
            )
            self.memory.backlog.update(
                item.id,
                status=pause_status,
                finished_ts=time.time(),
                last_error=stop_reason,
                outcome=pause_outcome,
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
                "outcome": pause_outcome,
                "stop_kind": stop_kind,
                "recoverable": True,
                "cost_usd": usd,
                "known_cost_usd": known_usd,
                "pricing_status": usage_summary.pricing_status,
                "spent_usd": known_usd,
                "context_packet": (
                    str(context_packet_path.parent / "latest.json")
                    if context_packet_path is not None
                    else ""
                ),
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
                "context_packet": (
                    str(context_packet_path.parent / "latest.json")
                    if context_packet_path is not None
                    else ""
                ),
            }

        repair_settlement: dict[str, Any] | None = None
        if recovered_repair_settlement is not None:
            repair_settlement = recovered_repair_settlement
            if not bool(repair_settlement.get("accepted")):
                success = False
                status = "error"
                stop_kind = "permanent_error"
                guard_errors = list(repair_settlement.get("guard_errors") or [])
                stop_reason = (
                    "restricted validator repair rejected"
                    + (": " + "; ".join(guard_errors) if guard_errors else "")
                )
        elif repair_capability is not None and repair_store is not None:
            reviewer_status = str(
                getattr(outcome, "final_review_status", "") or ""
            ).strip().lower()
            failure_source = str(
                getattr(outcome, "failure_source", "") or ""
            ).strip().lower()
            reviewer_accepted = bool(
                success
                and status == "done"
                and reviewer_status == "done"
                and not failure_source
            )
            try:
                repair_settlement = repair_store.close_repair_capability(
                    capability_id=str(repair_capability["capability_id"]),
                    nonce=str(repair_capability["nonce"]),
                    identity=repair_identity,
                    accepted=reviewer_accepted,
                    reason=(
                        str(getattr(outcome, "stop_reason", "") or "")
                        or f"Reviewer status={reviewer_status or 'missing'}; "
                        f"failure_source={failure_source or 'none'}"
                    ),
                )
            except (OSError, TypeError, ValueError) as exc:
                repair_settlement = {
                    "status": "rejected",
                    "accepted": False,
                    "guard_errors": [f"{type(exc).__name__}: {exc}"],
                }
            if not bool(repair_settlement.get("accepted")):
                success = False
                status = "error"
                stop_kind = "permanent_error"
                guard_errors = list(repair_settlement.get("guard_errors") or [])
                stop_reason = (
                    "restricted validator repair rejected"
                    + (": " + "; ".join(guard_errors) if guard_errors else "")
                )

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
        unfinished_plan_nodes: list[BacklogItem] = []
        if planner_bounded_node and item.plan_id:
            try:
                unfinished_plan_nodes = [
                    sibling
                    for sibling in self.memory.backlog.all()
                    if sibling.id != item.id
                    and sibling.plan_id == item.plan_id
                    and sibling.plan_version == item.plan_version
                    and sibling.status
                    not in {"done", "failed", "skipped", "superseded"}
                ]
            except Exception:  # noqa: BLE001 - stage safety falls back to Manager
                log.exception(
                    "life supervisor: failed to inspect dynamic plan before stage guard"
                )
        # A Planner DAG is authored entirely inside the current-stage frontier;
        # the Planner is forbidden to enqueue speculative downstream-stage work.
        # Therefore an intermediate node must not let the Manager advance the
        # project while sibling/dependent nodes from the same plan are unfinished.
        # The Manager decision has already mutated PIPELINE_STATE by this point,
        # so undo that premature advance and expose a HOLD transition locally.
        if (
            stage_action == "advance"
            and pipeline_stage_at_start
            and unfinished_plan_nodes
        ):
            live_stage = self._current_pipeline_stage() or pipeline_stage_at_start
            guard_reason = (
                f"dynamic plan {item.plan_id} still has unfinished current-stage "
                "node(s): "
                + ", ".join(node.title for node in unfinished_plan_nodes[:6])
            )
            guard_applied = live_stage == pipeline_stage_at_start
            if not guard_applied:
                try:
                    from ...skills.stage_checklists import rollback_stage

                    rollback_stage(
                        self._artifact_root(),
                        target_stage=pipeline_stage_at_start,
                        reason=guard_reason,
                        rolled_back_by="supervisor_dynamic_plan_guard",
                    )
                    guard_applied = True
                except Exception:  # noqa: BLE001
                    log.exception(
                        "life supervisor: failed to undo premature dynamic-plan "
                        "stage advance"
                    )
            if guard_applied:
                self._emit({
                    "type": EventType.LIFE_MANAGER_STAGE_DECISION,
                    "action": "rollback",
                    "target_stage": pipeline_stage_at_start,
                    "reason": guard_reason,
                    "current_stage": live_stage,
                    "source": "supervisor_dynamic_plan_guard",
                    "diagnostic": "unfinished_same_plan_nodes",
                    "item_id": item.id,
                    "plan_id": item.plan_id,
                    "unfinished_item_ids": [
                        node.id for node in unfinished_plan_nodes
                    ],
                })
                stage_transition = {
                    "action": "hold",
                    "current_stage": pipeline_stage_at_start,
                    "target_stage": pipeline_stage_at_start,
                    "reason": guard_reason,
                    "source": "supervisor_dynamic_plan_guard",
                    "diagnostic": "unfinished_same_plan_nodes",
                }
                stage_action = "hold"
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
        intentional_abort = status == "aborted" or stop_kind == "operator_abort"
        if intentional_abort:
            success = False
            status = "aborted"
        stage_reconciled_replan = (
            replan_requested and stage_action in {"advance", "rollback"}
        )
        err = exc_str or stop_reason or "unspecified failure"
        resumable = bool(
            research_pause or stop_kind_is_recoverable(stop_kind)
        )
        outcome_dimensions = mission_outcome_dimensions(
            status=status,
            success=success,
            review_status=str(
                getattr(outcome, "final_review_status", "") or ""
            ),
            stage_transition=stage_transition,
            scientific_decision=str(
                getattr(outcome, "scientific_decision", "") or ""
            ),
            failure_source=str(
                getattr(outcome, "failure_source", "") or ""
            ),
            stop_kind=stop_kind,
            resumable=resumable,
        )

        # Update backlog row. A bounded research cycle that did not achieve its
        # persisted success target is resumable, not a success or terminal failure.
        if success:
            self.memory.backlog.mark_done(item.id, outcome=outcome_dimensions)
        elif stage_reconciled_replan:
            self.memory.backlog.mark_failed(
                item.id,
                error=(
                    f"manager {stage_action} to "
                    f"{stage_transition.get('target_stage') or 'another stage'} "
                    "after Reviewer identified an upstream stage defect"
                ),
                outcome=outcome_dimensions,
            )
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
                outcome=outcome_dimensions,
            )
        elif intentional_abort:
            self.memory.backlog.update(
                item.id,
                status="aborted",
                finished_ts=time.time(),
                last_error=stop_reason,
                outcome=outcome_dimensions,
            )
        else:
            self.memory.backlog.mark_failed(
                item.id,
                error=err,
                outcome=outcome_dimensions,
            )

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
                    self._emit({
                        "type": EventType.LIFE_OPERATOR_QUESTION_PENDING,
                        "item_id": item.id,
                        "title": item.title,
                        "question": operator_question,
                        "agent_layer": "manager",
                    })
                except Exception:  # noqa: BLE001
                    log.exception(
                        "life supervisor: failed to persist pending_question"
                    )

        kind = (
            "mission_complete"
            if success
            else "mission_replan_requested"
            if replan_requested
            else "mission_aborted"
            if intentional_abort
            else "mission_failed"
        )
        final_submission_certified = bool(
            kind == "mission_complete"
            and item_scope == PLANNER_SCOPE_FINAL_SUBMISSION
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
        from ...core.claim_synthesis import build_claim_synthesis

        claim_synthesis = build_claim_synthesis(
            research_result=research_result,
            planner_report=planner_report,
            step_back=step_back,
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
            "scope": item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
            "success": success,
            "status": status,
            "outcome_class": mission_outcome_class(status=status, success=success),
            "outcome": outcome_dimensions,
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
            "resumable": resumable,
            "recoverable": bool(
                getattr(outcome, "recoverable", False)
                or stop_kind_is_recoverable(stop_kind)
            ),
            "stop_kind": stop_kind,
            "stop_reason": (
                stop_reason or err
                if kind in {"mission_failed", "mission_aborted"}
                else ""
            ),
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
            "claim_synthesis": claim_synthesis,
            "planner_report": planner_report,
            "checklist_feedback": checklist_feedback,
            "step_back": step_back,
            "context_packet": (
                str(context_packet_path.parent / "latest.json")
                if context_packet_path is not None
                else ""
            ),
            "final_submission_certified": final_submission_certified,
            "repair_capability": {
                "capability_id": str(repair_capability.get("capability_id") or ""),
                "authorization_id": str(repair_capability.get("authorization_id") or ""),
                "status": str((repair_settlement or {}).get("status") or ""),
                "accepted": bool((repair_settlement or {}).get("accepted", False)),
                "guard_errors": list((repair_settlement or {}).get("guard_errors") or []),
            } if repair_capability is not None else None,
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
            "claim_synthesis": claim_synthesis,
            "expected_plan_id": item.plan_id,
            "expected_plan_version": item.plan_version,
            "context_packet": (
                str(context_packet_path.parent / "latest.json")
                if context_packet_path is not None
                else ""
            ),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------


__all__ = ["MissionExecutionMixin"]
