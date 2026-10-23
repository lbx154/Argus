from __future__ import annotations

from types import SimpleNamespace

from argus.life.supervisor._constants import PLAN_RETRY, PROJECT_REPORT_MODEL_ATTEMPTS
from argus.life.supervisor._planning_context import PlanningContextMixin
from argus.life.supervisor._planning_cycle_completion import (
    PlanningCycleCompletionMixin,
)
from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus.planner import PlannerVerdict


class _CompletionHarness(PlanningCycleCompletionMixin):
    def __init__(self, tmp_path) -> None:
        self.root = tmp_path
        self.config = SimpleNamespace(open_ended=False)
        self.memory = SimpleNamespace(journal=SimpleNamespace(all=lambda: []))
        self.feedback: list[dict[str, str]] = []

    def _artifact_root(self):
        return self.root

    def _project_workdir(self):
        return self.root

    def _current_pipeline_stage(self) -> str:
        return "submission"

    def _effective_final_certification_gate(self, _root) -> bool:
        return True

    def _journal_has_final_certification(self) -> bool:
        return False

    def _final_submission_signature(self) -> str:
        return ""

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
        new_tasks=[],
        summary="The paper is complete.",
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
    from argus.skills import vertical_select

    monkeypatch.setattr(
        vertical_select,
        "vertical_has_current_completion_certificate",
        lambda *_args: False,
    )
    harness = _ManagerCompletionHarness(tmp_path)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        new_tasks=[],
        summary="Planner says done",
    )

    result = harness._pc_normalize_project_done(state)

    assert result == PLAN_RETRY
    assert harness.planner_verdicts == 0
    assert harness.feedback[-1]["diagnostic"] == "manager_final_stage_not_completed"


def test_manager_reports_all_stages_after_project_completion(
    tmp_path,
    monkeypatch,
) -> None:
    from argus.skills import vertical_select

    monkeypatch.setattr(
        vertical_select,
        "vertical_has_current_completion_certificate",
        lambda *_args: True,
    )
    harness = _ManagerCompletionHarness(tmp_path)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        new_tasks=[],
        summary="Planner says done",
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
    from argus.core.transcript import read_turns

    assert "covering every stage" in read_turns(tmp_path)[-1]["text"]
    assert any(
        event.get("type") == "life.manager.project_report"
        and event.get("stage_count") == 2
        for event in harness.events
    )


class _FailingReportHarness(_ManagerCompletionHarness):
    """A completion report that keeps failing, retried at the daemon's pace."""

    from argus.life.supervisor._core import LifeSupervisor
    from argus.life.supervisor._idle_cycle import IdleCycleMixin

    _enter_idle_backoff = IdleCycleMixin._enter_idle_backoff
    _idle_backoff_seconds = IdleCycleMixin._idle_backoff_seconds
    _operator_chinese = LifeSupervisor._operator_chinese
    _operator_words = LifeSupervisor._operator_words

    def __init__(self, tmp_path, *, manager_fails: bool) -> None:
        super().__init__(tmp_path)
        self.manager_fails = manager_fails
        self.manager_calls = 0
        self._consecutive_idle_planner_cycles = 0
        self._idle_since = None
        self._suggested_sleep_s = 0.0

    def _bound_manager(self):
        harness = self

        class Manager:
            def report_project_completion(self, **_kwargs):
                harness.manager_calls += 1
                if harness.manager_fails:
                    raise RuntimeError("manager backend down")
                return "Manager project report covering every stage."

        return Manager()

    def retry_for(self, seconds: float, poll_interval: float = 5.0) -> int:
        """Retry the report as the daemon would for ``seconds``; return attempts."""
        attempts, elapsed = 0, 0.0
        while elapsed < seconds:
            attempts += 1
            if self._manager_publish_project_report("done") == "reported":
                break
            elapsed += max(poll_interval, self._suggested_sleep_s)
        return attempts


def _alerts(harness) -> list[dict]:
    return [event for event in harness.events if event.get("type") == "operator_alert"]


def test_a_failing_manager_report_backs_off_and_alerts_once(tmp_path) -> None:
    harness = _FailingReportHarness(tmp_path, manager_fails=True)

    attempts = harness.retry_for(3600)

    # At the daemon's 5 s poll this was 720 Manager calls an hour. The Manager
    # gets its attempts; the next one sends the stage summary without it.
    assert harness.manager_calls == PROJECT_REPORT_MODEL_ATTEMPTS
    assert attempts == harness.manager_calls + 1
    assert len(_alerts(harness)) == 1


def test_a_failing_publish_never_asks_the_manager_again(tmp_path, monkeypatch) -> None:
    from argus.core import operator_messages

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(operator_messages, "publish_operator_message", refuse)
    harness = _FailingReportHarness(tmp_path, manager_fails=False)

    attempts = harness.retry_for(3600)

    assert harness.manager_calls == 1
    assert attempts <= 20
    assert len(_alerts(harness)) == 1
    # The written report survives a restart: a fresh supervisor publishes it
    # without a second Manager call once publishing works again.
    monkeypatch.undo()
    restarted = _FailingReportHarness(tmp_path, manager_fails=True)
    assert restarted._manager_publish_project_report("done") == "reported"
    assert restarted.manager_calls == 0
