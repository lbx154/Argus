"""Post-mission learning/outcome-settlement phase for ``SkillLoop.run``.

Owns everything after the supervised round-loop returns a verdict: skill
reuse-effectiveness recording, Reviewer-authored skill evolution, reviewer-
certified achievement telemetry, project-wiki evolution, per-mission skill
cost/outcome telemetry events, and final ``LoopOutcome`` assembly.
Extracted verbatim from the historical "Step 4"/"Step 4c" tail of
``SkillLoop.run``.
"""
from __future__ import annotations

import logging

from ..core.event_catalog import EventType
from ..core.models import LoopOutcome
from ..core.stop_kinds import stop_kind_is_recoverable
from .loop_state import MissionContext, SkillSelectionState

log = logging.getLogger(__name__)

# Reviewed ineffective uses are retained as evidence for later Reviewer-authored
# update/archive decisions. External/economic aborts remain neutral.
_INEFFECTIVE_SKILL_STATUSES: frozenset[str] = frozenset({"no_progress", "max_rounds"})


class MissionSettlementMixin:
    """Post-mission settlement phase methods for ``SkillLoop``."""

    def _settle_mission_outcome(
        self,
        mission: MissionContext,
        state: SkillSelectionState,
        status: str,
        rounds: list,
        final_message: str,
        reason: str,
        last_thread_id: str | None,
    ) -> LoopOutcome:
        # Step 4: learn from the OUTCOME. A called Reviewer may already have
        # edited durable memory; a self-approved Engineer may instead have used
        # its same-session maintenance continuation. Here we record effectiveness
        # evidence and retain legacy proposal replay compatibility.
        if state.skill is not None:
            try:
                if status == "done":
                    self.skill_store.record_reuse(
                        state.skill,
                        task_desc=mission.skill_task,
                        success=True,
                        on_event=self._emit,
                    )
                elif status in _INEFFECTIVE_SKILL_STATUSES:
                    self.skill_store.record_reuse(
                        state.skill,
                        task_desc=mission.skill_task,
                        success=False,
                        on_event=self._emit,
                    )
            except Exception as exc:  # noqa: BLE001 — never break the loop
                log.warning("skill use recording failed (%s: %s)",
                            type(exc).__name__, exc)

        try:
            from .evolution import evolve_skills_after_mission

            evolve_skills_after_mission(
                skill_store=self.skill_store,
                skill_router=self.skill_router,
                reviewer_runner=self.reviewer_runner,
                reviewer_model=self.config.resolved_reviewer_model(),
                reviewer_reasoning_effort=(
                    self.config.matcher_reasoning_effort or "high"
                ),
                rounds=rounds,
                task=mission.skill_task,
                apply_ops_enabled=(
                    self.config.skill_ops_enabled
                    and self.config.require_post_task_learning
                ),
                auto_compact_enabled=self.config.auto_compact_enabled,
                fallback_skills_dir=self.skills_dir,
                on_event=self.on_event,
            )
        except Exception:  # noqa: BLE001 - evolution must never shadow the verdict
            log.debug("skill evolution raised", exc_info=True)

        stop_kind = rounds[-1].stop_kind if rounds else None
        if status == "paused_budget" and stop_kind is None:
            stop_kind = "budget_exhausted"
        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            skill_used=state.skill_name,
            skill_distilled=state.skill_distilled,
            final_message=final_message,
            reason=reason,
            workdir=str(mission.workdir),
            last_thread_id=last_thread_id,
            stop_kind=stop_kind,
            recoverable=stop_kind_is_recoverable(stop_kind),
        )
        if self.config.wiki_enabled or self.config.auto_compact_enabled:
            try:
                from ..wiki.lifecycle import maintain_wikis_after_mission

                maintain_wikis_after_mission(
                    workdir=mission.workdir,
                    auto_compact_enabled=self.config.auto_compact_enabled,
                    reviewer_runner=self.reviewer_runner,
                    reviewer_model=self.config.resolved_reviewer_model(),
                    reviewer_reasoning_effort=(
                        self.config.matcher_reasoning_effort or "high"
                    ),
                    on_event=self.on_event,
                )
            except Exception:  # noqa: BLE001 - wiki maintenance must never block
                log.debug("wiki maintenance raised", exc_info=True)
        # Effectiveness telemetry — one structured event per mission so
        # operators can compute hit-rate, mean-rounds-with-skill, and
        # mean-rounds-without-skill from events.jsonl alone.
        try:
            matcher_model = str(
                getattr(
                    self.skill_store,
                    "matcher_model",
                    self.config.resolved_matcher_model(),
                )
                or self.config.resolved_matcher_model()
            )
            transfer_used = bool(
                state.distill_result is not None
                and not state.skill_distilled
            )
            distiller_model = str(
                self.config.resolved_skill_adapter_model()
                if transfer_used and not state.skill_distilled
                else (self.config.engineer_model or "")
            )
            distiller_input_tokens = int(getattr(state.distill_result, "input_tokens", 0) or 0)
            distiller_cached_input_tokens = int(
                getattr(state.distill_result, "cached_input_tokens", 0) or 0
            )
            distiller_output_tokens = int(
                getattr(state.distill_result, "output_tokens", 0) or 0
            )
            distiller_reasoning_output_tokens = int(
                getattr(state.distill_result, "reasoning_output_tokens", 0) or 0
            )
            matcher_usage = {
                "model": matcher_model,
                "input_tokens": int(state.matcher_input_tokens or 0),
                "cached_input_tokens": int(state.matcher_cached_input_tokens or 0),
                "output_tokens": int(state.matcher_output_tokens or 0),
                "reasoning_output_tokens": int(
                    getattr(state.match, "reasoning_output_tokens", 0) or 0
                ),
            }
            distiller_usage = {
                "model": distiller_model,
                "input_tokens": distiller_input_tokens,
                "cached_input_tokens": distiller_cached_input_tokens,
                "output_tokens": distiller_output_tokens,
                "reasoning_output_tokens": distiller_reasoning_output_tokens,
            }
            self._emit({
                "type": EventType.SKILL_COST_COMPLETED,
                "agent_layer": "skill_transfer" if transfer_used else "scientist",
                "matcher_model": matcher_model,
                "distiller_model": distiller_model,
                "matcher": matcher_usage,
                "distiller": distiller_usage,
                "matcher_input_tokens": matcher_usage["input_tokens"],
                "matcher_cached_input_tokens": matcher_usage["cached_input_tokens"],
                "matcher_output_tokens": matcher_usage["output_tokens"],
                "matcher_reasoning_output_tokens": matcher_usage["reasoning_output_tokens"],
                "distiller_input_tokens": distiller_usage["input_tokens"],
                "distiller_cached_input_tokens": distiller_usage["cached_input_tokens"],
                "distiller_output_tokens": distiller_usage["output_tokens"],
                "distiller_reasoning_output_tokens": distiller_usage["reasoning_output_tokens"],
                "input_tokens": (
                    matcher_usage["input_tokens"] + distiller_usage["input_tokens"]
                ),
                "cached_input_tokens": (
                    matcher_usage["cached_input_tokens"]
                    + distiller_usage["cached_input_tokens"]
                ),
                "output_tokens": (
                    matcher_usage["output_tokens"] + distiller_usage["output_tokens"]
                ),
                "reasoning_output_tokens": (
                    matcher_usage["reasoning_output_tokens"]
                    + distiller_usage["reasoning_output_tokens"]
                ),
                # Native Copilot spend from BOTH routing calls. These used to
                # disappear from mission cost entirely: SkillMatch carried only
                # tokens, while SkillScientist returned only markdown.
                "premium_requests": float(state.matcher_premium_requests or 0.0)
                + float(getattr(state.distill_result, "premium_requests", 0.0) or 0.0),
                "usage_scope": "delta",
            })
            self._emit({
                "type": EventType.SKILL_OUTCOME,
                "skill_name": state.skill_name or "",
                "skill_hit": bool(state.strict_skill_hit),
                "nearest_transfer_fallback": bool(state.nearest_transfer_fallback),
                "low_confidence_transfer_hint": bool(
                    state.low_confidence_transfer_hint
                ),
                "skill_distilled": bool(state.skill_distilled),
                "matcher_model": matcher_model,
                "distiller_model": distiller_model,
                "matcher_tokens": int(state.matcher_tokens or 0),
                "matcher_input_tokens": state.matcher_input_tokens,
                "matcher_cached_input_tokens": state.matcher_cached_input_tokens,
                "matcher_output_tokens": state.matcher_output_tokens,
                "distiller_tokens": int(
                    distiller_input_tokens + distiller_output_tokens
                ),
                "distiller_input_tokens": distiller_input_tokens,
                "distiller_cached_input_tokens": distiller_cached_input_tokens,
                "distiller_output_tokens": distiller_output_tokens,
                "rounds": int(len(rounds)),
                "status": str(status),
                "success": bool(status == "done"),
            })
        except Exception:  # noqa: BLE001
            log.debug("skill.outcome emit failed", exc_info=True)
        self._emit({
            "type": EventType.LOOP_DONE,
            "text": f"status={status} rounds={len(rounds)} reason={reason[:80]}",
        })
        return outcome
