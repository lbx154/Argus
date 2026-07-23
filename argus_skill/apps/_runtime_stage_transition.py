"""Stage-transition mixin: ``StageTransitionMixin`` — hands each round's
reviewer verdict to the Manager (the sole post-bootstrap writer of the
pipeline stage) and returns its advance/hold/rollback decision.

Split out of ``_runtime.py`` so that module stays under the maintainability
line-count target. Every name here is re-exported from ``_runtime.py`` (see
its module docstring and ``__all__``) so external imports are unaffected.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.ports import EventSink
from ..core.research_direction import normalize_research_direction

log = logging.getLogger(__name__)


class StageTransitionMixin:
    """Post-mission stage-decision half of ``_SkillLoopRunner``."""

    def _decide_stage_transition(
        self,
        *,
        rounds_list: list,
        workdir: Path,
        sink: EventSink,
        root_task_id: str | None = None,
        mission_scope: str = "",
        open_ended: bool = False,
        continuous_objective: str = "",
    ) -> dict:
        """Hand this round's reviewer verdict to the Manager — the SOLE
        post-bootstrap writer of the pipeline stage — and let it judge
        advance / hold / rollback and write ``PIPELINE_STATE.json``.

        Reviewer/planner only advise; the engineer no longer edits stage state.
        Fail-open: a stage decision must NEVER break a mission — any error
        degrades to a no-op (the stage simply stays put this round). Returns the
        decision dict (empty on skip/error) for the ``_Outcome`` / journal; the
        stage write itself already happened inside ``decide_stage_transition``.
        """
        try:
            from ..manager import Manager

            final_review = getattr(rounds_list[-1], "review", None) if rounds_list else None
            st = Manager(
                project_root=getattr(self, "_artifact_root", workdir),
                runner=getattr(self, "manager_backend", None) or self._backend,
                skill_store=getattr(self, "_manager_skill_store", None),
                manager_session_root=getattr(self, "_manager_session_root", workdir),
                usage_context=self.task_usage_context,
            ).decide_stage_transition(
                review=final_review,
                project_root=getattr(self, "_artifact_root", workdir),
                on_event=sink.handle_event,
                root_task_id=root_task_id,
                mission_scope=mission_scope,
                open_ended=open_ended,
                continuous_objective=continuous_objective,
            )
            decision = {
                "action": st.action,
                "target_stage": st.target_stage,
                "reason": st.reason,
                "current_stage": st.current_stage,
                "source": st.source,
                "diagnostic": st.diagnostic,
            }
            final_review_status = str(getattr(final_review, "status", "") or "").strip().lower()
            if st.action != "hold" or final_review_status in {"done", "blocked"}:
                try:
                    import hashlib
                    import json

                    from ..manager.control_state import CampaignControlStore

                    state_root = Path(getattr(self, "_manager_session_root", workdir))
                    control = CampaignControlStore(
                        state_root,
                        project_root=getattr(self, "_artifact_root", workdir),
                    )
                    identity = control.campaign_identity(
                        objective=continuous_objective,
                    )
                    pipeline_path = (
                        Path(getattr(self, "_artifact_root", workdir))
                        / "research"
                        / "PIPELINE_STATE.json"
                    )
                    try:
                        pipeline_bytes = pipeline_path.read_bytes()
                        pipeline_sha256 = hashlib.sha256(pipeline_bytes).hexdigest()
                    except OSError:
                        pipeline_sha256 = "missing"
                    review_projection = {
                        "status": final_review_status,
                        "final_submission_certified": bool(
                            getattr(
                                final_review,
                                "final_submission_certified",
                                False,
                            )
                        ),
                        "scientific_decision": normalize_research_direction(
                            getattr(final_review, "scientific_decision", "")
                        ),
                        "failure_source": str(getattr(final_review, "failure_source", "") or ""),
                        "failure_layer": str(getattr(final_review, "failure_layer", "") or ""),
                        "failure_source_evidence": list(
                            getattr(final_review, "failure_source_evidence", []) or []
                        ),
                        "validator_id": str(getattr(final_review, "validator_id", "") or ""),
                        "repair_paths": list(getattr(final_review, "repair_paths", []) or []),
                    }
                    review_sha256 = hashlib.sha256(
                        json.dumps(
                            review_projection,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    control_head = control.clear_wait_for_new_evidence(
                        identity=identity,
                        stage_projection={
                            "action": st.action,
                            "current_stage": st.current_stage,
                            "target_stage": st.target_stage,
                            "pipeline_state_sha256": pipeline_sha256,
                        },
                        terminal_evidence=[
                            {
                                **review_projection,
                                "sha256": review_sha256,
                            }
                        ],
                        reason="Manager committed stage and terminal review state",
                    )
                    decision["campaign_epoch"] = control_head.campaign_epoch
                    decision["state_revision"] = control_head.state_revision
                except Exception:  # noqa: BLE001 - projection cannot own verdict
                    log.debug(
                        "manager control revision projection skipped",
                        exc_info=True,
                    )
            sink.handle_event({"type": "life.manager.stage_decision", **decision})
            return decision
        except Exception:  # noqa: BLE001 — stage decision must never break a mission
            log.debug("manager stage decision skipped", exc_info=True)
            return {}
