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


class _ManagerCompletionHarness(_CompletionHarness):
    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.config = SimpleNamespace(
            open_ended=False,
            continuous_objective="finish the project",
        )
        self.events: list[dict] = []
        self.planner_verdicts = 0
        self.report_context = None
        self._planning_cycles = 1
        self.memory = SimpleNamespace(
            root=tmp_path,
            journal=SimpleNamespace(all=lambda: [], tail=lambda _n: []),
            backlog=SimpleNamespace(history=lambda: []),
        )
        self.sink = SimpleNamespace(handle_event=lambda _event: None)

    def _journal_has_final_certification(self) -> bool:
        return True

    def _manager_project_completion_context(self):
        return {
            "current_stage": "submission",
            "stages": {
                "research": {"status": "done"},
                "submission": {"status": "done"},
            },
            "stage_history": [{"from_stage": "research", "to_stage": "submission"}],
            "rollback_history": [],
            "stage_reviews": {
                "research": {"review_status": "done"},
                "submission": {"review_status": "done"},
            },
        }

    def _bound_manager(self):
        harness = self

        class Manager:
            def report_project_completion(self, **kwargs):
                harness.report_context = kwargs["completion_context"]
                return "Manager project report covering every stage."

        return Manager()

    def _emit(self, event) -> bool:
        self.events.append(event)
        return True

    def _clear_manager_planner_feedback(self) -> None:
        pass

    def _emit_planner_verdict(self, **kwargs) -> bool:
        self.planner_verdicts += 1
        self.events.append({"type": "life.planner.verdict", "project_done": True})
        return (
            self._manager_publish_project_report(str(kwargs.get("reason") or ""))
            == "reported"
        )

    def _open_ended_terminal_idle_signature(self) -> str:
        return ""


def test_planner_cannot_complete_before_manager_finishes_final_stage(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.skills import vertical_select

    monkeypatch.setattr(
        vertical_select,
        "vertical_has_current_completion_certificate",
        lambda *_args: False,
    )
    harness = _ManagerCompletionHarness(tmp_path)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        waiting=False,
        new_tasks=[],
        reason="Planner says done",
    )

    result = harness._pc_normalize_project_done(state)

    assert result == PLAN_RETRY
    assert harness.planner_verdicts == 0
    assert harness.feedback[-1]["diagnostic"] == "manager_final_stage_not_completed"


def test_manager_reports_all_stages_after_project_completion(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.skills import vertical_select

    monkeypatch.setattr(
        vertical_select,
        "vertical_has_current_completion_certificate",
        lambda *_args: True,
    )
    harness = _ManagerCompletionHarness(tmp_path)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        waiting=False,
        new_tasks=[],
        reason="Planner says done",
    )

    result = harness._pc_normalize_project_done(state)

    assert result is False
    assert harness.planner_verdicts == 1
    assert set(harness.report_context["stages"]) == {"research", "submission"}
    assert harness.report_context["stage_history"]
    assert set(harness.report_context["stage_reviews"]) == {
        "research",
        "submission",
    }
    project_event_index = next(
        index
        for index, event in enumerate(harness.events)
        if event.get("type") == "life.planner.verdict"
    )
    report_event_index = next(
        index
        for index, event in enumerate(harness.events)
        if event.get("type") == "life.manager.project_report"
    )
    assert project_event_index < report_event_index
    from argus_skill.core.transcript import read_turns

    assert "covering every stage" in read_turns(tmp_path)[-1]["text"]
    assert any(
        event.get("type") == "life.manager.project_report"
        and event.get("stage_count") == 2
        for event in harness.events
    )
