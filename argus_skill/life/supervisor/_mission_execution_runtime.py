"""Mission lifecycle phases: claim/context setup and runner invocation.

``MissionExecutionRuntimeMixin`` covers the first half of one claimed
backlog item's life: building its prelude/context packet/cost sink, invoking
the skill-loop runner (including the restricted validator-repair capability
claim), and deriving the basic outcome fields (success/status/stop_kind) plus
the budget/provider pause short-circuit. The second half (repair-capability
settlement, dynamic-plan stage guard, final status + journal) lives in
``_mission_execution_settlement.py``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.stop_kinds import normalize_stop_kind, pause_status_for_stop_kind
from ...core.usage import UsageLedger, UsageRecord
from ..memory import BacklogItem
from ..mission_outcome import mission_outcome_class, mission_outcome_dimensions
from ._cost import _CostTrackingSink
from ._mission_execution_helpers import (
    _MissionRunState,
    bounded_dag_node_max_rounds,
    is_progressive_experiment_matrix,
)

log = logging.getLogger(__name__)


class MissionExecutionRuntimeMixin:
    """Claim/context setup and runner invocation for one mission."""

    # ------------------------------------------------------------------
    # Phase: claim/context
    # ------------------------------------------------------------------

    def _build_mission_prelude(self, item: BacklogItem) -> str:
        prelude = self.memory.render_prelude()
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = (
                item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
            )
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        return prelude

    def _prepare_mission_context(
        self, item: BacklogItem, prelude: str,
    ) -> _MissionRunState:
        """Build per-mission context: packet, cost sink, telemetry, isolation.

        Emits ``LIFE_MISSION_STARTED``. Returns the scratch state that the
        rest of the lifecycle phases read from and write to.
        """
        state = _MissionRunState(item)
        state.prelude = prelude
        state.pipeline_stage_at_start = self._current_pipeline_stage() or ""
        state.usage_attempt_id = f"{item.id}:attempt:{max(1, int(item.attempt or 1))}"
        self._missions_started += 1
        state.item_scope = self._planner_scope_from_item(item)

        self._emit({
            "type": EventType.LIFE_MISSION_STARTED,
            "item_id": item.id,
            "title": item.title,
            # Carry the objective on the event itself (not just the journal
            # entry) so the live mission-context line renders the
            # real goal instead of "objective=-".
            "objective": item.objective,
            "scope": state.item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
            "missions_started": self._missions_started,
            "attempt": item.attempt,
            "usage_attempt_id": state.usage_attempt_id,
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

        state.usage_root = Path(
            getattr(self.memory, "project_root", None)
            or getattr(self.memory, "root", None)
            or self._artifact_root()
        )
        try:
            from ..context_packet import create_mission_context

            state.context_packet_path = create_mission_context(
                life_dir=state.usage_root,
                mission_id=item.id,
                stage=state.pipeline_stage_at_start,
                scope=state.item_scope,
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
        state.usage_ledger = (
            UsageLedger(state.usage_root)
            if hasattr(self.runner, "_set_usage_context")
            else None
        )
        state.cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
            usage_ledger=state.usage_ledger,
            mission_id=state.usage_attempt_id,
        )

        state.item_tags = {
            str(tag).strip().lower()
            for tag in getattr(item, "tags", [])
        }
        state.execution_workdir = self._project_workdir()
        state.configured_execution_workdir = str(
            getattr(item, "execution_workdir", "") or ""
        ).strip()
        if state.configured_execution_workdir:
            if "framework_maintenance" not in state.item_tags:
                raise ValueError(
                    "execution_workdir is reserved for framework maintenance"
                )
            state.execution_workdir = Path(
                state.configured_execution_workdir
            ).expanduser().resolve()
            if not state.execution_workdir.is_dir():
                raise ValueError("framework maintenance worktree is unavailable")
        if self.config.telemetry_dir is not None:
            try:
                from ..telemetry import MissionTelemetryMonitor
                state.telemetry_monitor = MissionTelemetryMonitor(
                    life_dir=self.config.telemetry_dir,
                    workdir=state.execution_workdir,
                    item_id=item.id,
                    title=item.title,
                    interval_seconds=self.config.telemetry_interval_seconds,
                    stop_event=self.config.stop_event,
                )
                state.telemetry_monitor.start()
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to start telemetry monitor")

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
        return state

    # ------------------------------------------------------------------
    # Phase: runner invocation (+ restricted validator-repair capability)
    # ------------------------------------------------------------------

    def _invoke_mission_runner(self, state: _MissionRunState) -> None:
        """Call ``self.runner.execute(...)`` and record the raw outcome.

        Mutates ``state`` in place (``outcome``, ``exc_str``, ``elapsed``,
        the repair-capability trio). Telemetry is stopped in the ``finally``
        clause regardless of outcome.
        """
        item = state.item
        state.t0 = time.time()
        try:
            execute_kwargs: dict[str, Any] = {
                "objective": item.objective,
                "sink": state.cost_sink,
                "prelude_context": state.prelude,
                "scope": state.item_scope,
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

                state.repair_store = CampaignControlStore(
                    Path(self.memory.root),
                    project_root=self._project_workdir(),
                )
                existing = state.repair_store.current_repair_capability(
                    mission_id=item.id,
                )
                if existing is not None:
                    if (
                        existing.get("authorization_id") != authorization_id
                        or existing.get("action") != authorization_action
                    ):
                        raise ValueError("running repair capability does not match backlog")
                    state.repair_identity = state.repair_store.campaign_identity(
                        campaign_epoch=int(existing.get("campaign_epoch") or 0),
                    )
                    state.repair_capability = existing
                    if existing.get("event") == "closed":
                        state.recovered_repair_settlement = existing
                else:
                    authorization = state.repair_store.get_authorization(authorization_id)
                    if authorization is None:
                        raise ValueError("Manager authorization is unavailable")
                    state.repair_identity = state.repair_store.campaign_identity(
                        campaign_epoch=int(authorization.get("campaign_epoch") or 0),
                    )
                    claimed = state.repair_store.claim_repair_capability(
                        authorization_id=authorization_id,
                        nonce=str(authorization.get("nonce") or ""),
                        action=authorization_action,
                        identity=state.repair_identity,
                        mission_id=item.id,
                    )
                    state.repair_capability = {
                        name: getattr(claimed, name)
                        for name in claimed.__dataclass_fields__
                    }
                if state.repair_capability.get("status") == "claimed":
                    started = state.repair_store.begin_acceptance_retry(
                        capability_id=str(state.repair_capability["capability_id"]),
                        nonce=str(state.repair_capability["nonce"]),
                        identity=state.repair_identity,
                    )
                    state.repair_capability = {
                        name: getattr(started, name)
                        for name in started.__dataclass_fields__
                    }
                public_repair = (
                    "## Restricted validator repair capability\n"
                    f"- authorization_id: {authorization_id}\n"
                    f"- capability_id: {state.repair_capability['capability_id']}\n"
                    f"- validator_id: {state.repair_capability['validator_id']}\n"
                    "- allowed_write_paths: "
                    + ", ".join(state.repair_capability.get("allowed_write_paths") or [])
                    + "\n- scientific evidence, preregistration, thresholds, and "
                    "success criteria are frozen. Edit only the listed paths. "
                    "Run the same acceptance checks once. Reviewer must compare "
                    "the old and new validator logic and reject any lowered "
                    "scientific standard."
                )
                execute_kwargs["prelude_context"] = (
                    public_repair + "\n\n---\n" + state.prelude
                    if state.prelude else public_repair
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
                    execute_kwargs["usage_mission_id"] = state.usage_attempt_id
                if "context_packet_path" in params or _accepts_kw:
                    execute_kwargs["context_packet_path"] = (
                        str(state.context_packet_path)
                        if state.context_packet_path else ""
                    )
                if "working_dir_override" in params or _accepts_kw:
                    execute_kwargs["working_dir_override"] = (
                        str(state.execution_workdir)
                        if state.configured_execution_workdir
                        else ""
                    )
                if "maintenance_mission" in params or _accepts_kw:
                    execute_kwargs["maintenance_mission"] = (
                        "framework_maintenance" in state.item_tags
                    )
                progressive_matrix = is_progressive_experiment_matrix(item)
                if (
                    "progressive_experiment_matrix" in params
                    or _accepts_kw
                ):
                    execute_kwargs["progressive_experiment_matrix"] = (
                        progressive_matrix
                    )
                if "bounded_dag_node" in state.item_tags and not progressive_matrix:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = (
                            bounded_dag_node_max_rounds()
                        )
                if state.repair_capability is not None:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = 1
                    if "workflow_mode_override" in params or _accepts_kw:
                        execute_kwargs["workflow_mode_override"] = "direct"
            except (TypeError, ValueError):
                execute_kwargs["original_objective"] = original_objective
                execute_kwargs["mission_id"] = item.id
                execute_kwargs["usage_mission_id"] = state.usage_attempt_id
                execute_kwargs["require_independent_review"] = (
                    self._item_requires_independent_review(item)
                )
                execute_kwargs["context_packet_path"] = (
                    str(state.context_packet_path) if state.context_packet_path else ""
                )
                if state.repair_capability is not None:
                    execute_kwargs["max_rounds_override"] = 1
                    execute_kwargs["workflow_mode_override"] = "direct"
            if state.recovered_repair_settlement is not None:
                from types import SimpleNamespace

                recovered_accepted = bool(
                    state.recovered_repair_settlement.get("accepted")
                )
                state.outcome = SimpleNamespace(
                    success=recovered_accepted,
                    status="done" if recovered_accepted else "error",
                    stop_reason=str(
                        state.recovered_repair_settlement.get("reason") or ""
                    ),
                    rounds=0,
                    final_review_status=(
                        "done" if recovered_accepted else "not_assessed"
                    ),
                    failure_source="",
                    stage_transition={},
                )
            else:
                state.outcome = self.runner.execute(**execute_kwargs)
        except Exception as exc:  # noqa: BLE001
            state.exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        finally:
            if state.telemetry_monitor is not None:
                try:
                    state.telemetry_monitor.stop()
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: failed to stop telemetry monitor")
        state.elapsed = time.time() - state.t0

    # ------------------------------------------------------------------
    # Phase: basic outcome derivation + budget/provider pause
    # ------------------------------------------------------------------

    def _derive_basic_outcome_fields(self, state: _MissionRunState) -> None:
        """Fill in success/status/stop_kind and settle mission-level bookkeeping.

        This covers skill evolution, the legacy usage-ledger fallback append,
        and the ``auth_failure`` advisory event — all independent of whatever
        happens next (pause / repair settlement / stage transitions).
        """
        outcome = state.outcome
        item = state.item
        state.success = bool(getattr(outcome, "success", False)) if outcome else False
        state.status = str(getattr(outcome, "status", "error") if outcome else "error")
        state.rounds = int(getattr(outcome, "rounds", 0) or 0)
        state.stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        state.stop_kind = normalize_stop_kind(getattr(outcome, "stop_kind", None))
        if state.status == "budget_exhausted" and state.stop_kind is None:
            state.stop_kind = "budget_exhausted"
        self._evolve_runtime_skills_after_mission(
            success=state.success,
            usage_mission_id=state.usage_attempt_id,
        )
        usage_summary = state.cost_sink.usage_summary()
        state.usage_summary = usage_summary
        state.usd = usage_summary.cost_usd
        state.known_usd = usage_summary.known_cost_usd
        if state.usage_ledger is None:
            # Deterministic/memory runners used by tests do not own real
            # ``run_exec`` calls. Persist their aggregate once so subsequent
            # budget checks still exercise the same ledger-only read path.
            UsageLedger(state.usage_root, migrate_legacy=False).append(
                UsageRecord(
                    call_id=f"memory-mission:{item.id}:{int(state.t0 * 1_000_000)}",
                    project_id=state.usage_root.name,
                    mission_id=state.usage_attempt_id,
                    provider="memory",
                    model="",
                    run_label="memory.mission.aggregate",
                    started_at=state.t0,
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
                    cost_usd=state.known_usd,
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
        state.auth_failure = bool(getattr(outcome, "auth_failure", False))
        if state.auth_failure:
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

    def _maybe_pause_for_recoverable_stop(
        self, state: _MissionRunState,
    ) -> dict[str, Any] | None:
        """Return a pause result dict, or ``None`` to continue the lifecycle."""
        outcome = state.outcome
        item = state.item
        pause_status = pause_status_for_stop_kind(state.stop_kind)
        if state.status == "budget_exhausted":
            state.status = "paused_budget"
            pause_status = state.status
        if not pause_status:
            return None
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
            failure_layer=str(
                getattr(outcome, "failure_layer", "") or ""
            ),
            stop_kind=state.stop_kind,
            resumable=True,
        )
        self.memory.backlog.update(
            item.id,
            status=pause_status,
            finished_ts=time.time(),
            last_error=state.stop_reason,
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
            "stop_kind": state.stop_kind,
            "recoverable": True,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "spent_usd": state.known_usd,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        })
        return {
            "status": pause_status,
            "item_id": item.id,
            "success": False,
            "stop_kind": state.stop_kind,
            "recoverable": True,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        }


__all__ = ["MissionExecutionRuntimeMixin"]
