"""Completion feedback keeps role prose natural and Host metadata exact."""

from __future__ import annotations

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._planning_context import PlanningContextMixin


def _note(diagnostic: str, reason: str = "gate held") -> str:
    class Harness(PlanningContextMixin):
        def _load_manager_planner_feedback(self):
            return {
                "stage": "scope",
                "diagnostic": diagnostic,
                "attempts": 1,
                "reason": reason,
            }

    return Harness()._manager_planner_feedback_runtime_note()


@pytest.mark.parametrize(
    "diagnostic",
    ["final_certification_missing", "research_target_incomplete"],
)
def test_certification_gates_name_the_only_action_that_clears_them(
    diagnostic: str,
) -> None:
    note = _note(diagnostic)

    assert "Host will record its final-submission scope" in note
    assert "TASK_SCOPE" not in note
    assert "harness does not prescribe" not in note


def test_the_research_gate_asks_for_natural_verification_work() -> None:
    note = _note(
        "research_target_incomplete",
        "Research project completion gate held: "
        "missing_exploratory_reviewer_certification.",
    )

    assert "Describe the next executable verification task naturally" in note


def test_stage_gate_records_host_owned_closing_metadata() -> None:
    note = _note("staged_goal_gate_incomplete")

    assert "Host will record it as stage-closing work" in note
    assert "TASK_SCOPE" not in note
    assert "harness does not prescribe" not in note


def test_unprescribed_diagnostics_still_leave_the_planner_its_judgement() -> None:
    note = _note("external_completion_gate_held")

    assert "harness does not prescribe" in note


def test_no_feedback_means_no_note() -> None:
    class Harness(PlanningContextMixin):
        def _load_manager_planner_feedback(self):
            return None

    assert Harness()._manager_planner_feedback_runtime_note() == ""


@pytest.mark.parametrize(
    ("diagnostic", "expected_scope"),
    [
        ("final_certification_missing", "scope:final_submission"),
        ("staged_goal_gate_incomplete", "scope:bounded"),
    ],
)
def test_feedback_writes_gate_metadata_during_enqueue(
    tmp_path,
    monkeypatch,
    diagnostic: str,
    expected_scope: str,
) -> None:
    class _Sink:
        def __init__(self) -> None:
            self.events = []

        def handle_event(self, event):
            self.events.append(event)

    class _Runner:
        def __init__(self) -> None:
            self.calls = 0

        def run_exec(self, **_kwargs):
            self.calls += 1
            return RunnerResult(
                exit_code=0,
                agent_messages=[
                    "\n".join(
                        [
                            "PROJECT_DONE=false",
                            "REASON=final certification remains",
                            "TASK_KEY=final-certification",
                            "TASK_TITLE=Make final certification host-visible",
                            "TASK_OBJECTIVE=Run the final independent verification.",
                            "TASK_ACCEPTANCE_CHECK=Reviewer PASS is recorded.",
                        ]
                    )
                ],
                stdout_lines=[],
                stderr_lines=[],
                thread_id=None,
                fatal_error=None,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
            )

    sink = _Sink()
    planner_runner = _Runner()
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"),
        runner=object(),
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="complete final submission certification",
            budget=LifeBudget(max_missions=1),
            final_certification_gate=True,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
        planner_runner=planner_runner,
    )
    monkeypatch.setattr(
        supervisor,
        "_maybe_idle_after_unchanged_open_ended_done",
        lambda: None,
    )
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(
        supervisor,
        "_render_journal_for_planner",
        lambda: _note(diagnostic),
    )
    monkeypatch.setattr(
        supervisor,
        "_load_manager_planner_feedback",
        lambda: {
            "active": True,
            "diagnostic": diagnostic,
            "reason": "gate held",
        },
    )
    monkeypatch.setattr(supervisor, "_clear_manager_planner_feedback", lambda: None)
    monkeypatch.setattr(supervisor, "_final_submission_scope_applies", lambda _root: True)
    monkeypatch.setattr(supervisor, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_planner_runtime_with_idle_note", lambda: "")

    assert supervisor._plan_next_work() is True
    assert planner_runner.calls == 1
    item = supervisor.memory.backlog.all()[0]
    assert expected_scope in item.tags
    assert "stage_closing" in item.tags
    assert "review:required" in item.tags
