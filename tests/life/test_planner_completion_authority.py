from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.planner import PlannerVerdict


class _CompletionHarness(PlanningCycleCompletionMixin):
    def __init__(self, tmp_path) -> None:
        self.root = tmp_path
        self.config = SimpleNamespace(open_ended=False)
        self.memory = SimpleNamespace(journal=SimpleNamespace(all=lambda: []))
        self.feedback: list[dict[str, str]] = []

    def _artifact_root(self):
        return self.root

    def _current_pipeline_stage(self) -> str:
        return "submission"

    def _effective_final_certification_gate(self, _root) -> bool:
        return True

    def _journal_has_final_certification(self) -> bool:
        return False

    def _persist_manager_planner_feedback(self, **payload) -> bool:
        self.feedback.append(payload)
        return True

    def _emit(self, _event) -> None:
        pass

    def _emit_status(self, _text: str) -> None:
        pass

    def _reset_idle_backoff(self) -> None:
        pass


def test_harness_returns_rejected_completion_to_planner_without_authoring_task(
    tmp_path,
) -> None:
    harness = _CompletionHarness(tmp_path)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        waiting=False,
        new_tasks=[],
        reason="The paper is complete.",
    )

    result = harness._pc_normalize_project_done(state)

    assert result == PLAN_RETRY
    assert state.verdict.new_tasks == []
    assert harness.feedback == [
        {
            "stage": "submission",
            "reason": "final submission requires an independent certification",
            "diagnostic": "final_certification_missing",
        }
    ]


def test_final_certification_feedback_requires_final_submission_scope() -> None:
    class FeedbackHarness(PlanningContextMixin):
        def _load_manager_planner_feedback(self):
            return {
                "stage": "submission",
                "diagnostic": "final_certification_missing",
                "attempts": 1,
                "reason": "final submission requires an independent certification",
            }

    note = FeedbackHarness()._manager_planner_feedback_runtime_note()

    assert "Host will record its final-submission scope" in note
    assert "TASK_SCOPE" not in note
    assert "which bounded tasks" not in note
