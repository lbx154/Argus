from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.planner import PlannerVerdict, TaskSpec
from argus_skill.skills.vertical_select import persist_vertical


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

    assert "`TASK_SCOPE=final_submission`" in note
    assert "successful Reviewer verdict" in note
    assert "which bounded tasks" not in note


def test_research_target_feedback_requires_final_submission_scope() -> None:
    class FeedbackHarness(PlanningContextMixin):
        def _load_manager_planner_feedback(self):
            return {
                "stage": "research",
                "diagnostic": "research_target_incomplete",
                "attempts": 1,
                "reason": "missing_exploratory_reviewer_certification",
            }

    note = FeedbackHarness()._manager_planner_feedback_runtime_note()

    assert "`TASK_SCOPE=final_submission`" in note
    assert "successful Reviewer verdict" in note


def test_finite_research_target_keeps_final_submission_transport(tmp_path) -> None:
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="exploratory",
    )

    class ScopeHarness(PlanningContextMixin):
        def _artifact_root(self):
            return tmp_path

        def _effective_final_certification_gate(self, _root) -> bool:
            return False

        def _normalize_planner_scope(self, value: str) -> str:
            return value or "bounded"

    tags = ScopeHarness()._planner_task_tags(
        TaskSpec(
            title="Certify exploratory target",
            objective="Independently review the completed research target.",
            scope="final_submission",
            stage_closing=True,
        )
    )

    assert "scope:final_submission" in tags
    assert "scope:bounded" not in tags
    assert "review:required" in tags
