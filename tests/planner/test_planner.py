from __future__ import annotations

from argus_skill.core.models import RunnerResult
from argus_skill.planner.planner import (
    NO_CONCRETE_TASKS_ERROR,
    Planner,
    PlannerConfig,
    parse_planner_text,
)
from argus_skill.roles.prompts.planner import _PLANNER_CORE_CONTRACT


def test_parse_key_value_completion_after_freeform_progress() -> None:
    verdict = parse_planner_text(
        "Implemented the source change and ran the focused tests.\n"
        "PROJECT_DONE=true\n"
        "REASON=Updated the parser and verified the regression suite."
    )

    assert verdict.error == ""
    assert verdict.project_done is True
    assert verdict.new_tasks == []
    assert verdict.reason == "Updated the parser and verified the regression suite."


def test_parse_status_summary_aliases() -> None:
    verdict = parse_planner_text(
        "STATUS=completed\nSUMMARY=Implementation and verification finished."
    )

    assert verdict.project_done is True
    assert verdict.reason == "Implementation and verification finished."


def test_incomplete_key_value_result_is_retryable() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\nREASON=External credential is still required."
    )

    assert verdict.project_done is False
    assert verdict.error == "planner said not done but produced no concrete tasks"


def test_missing_completion_marker_is_retryable() -> None:
    verdict = parse_planner_text("I inspected the repository but did not finish.")

    assert verdict.project_done is False
    assert verdict.error == "planner missing key-value completion marker"


def test_planner_prompt_requires_direct_edits_and_plain_key_values() -> None:
    assert "direct project executor" in _PLANNER_CORE_CONTRACT
    assert "edit project files" in _PLANNER_CORE_CONTRACT
    assert "Do not stop after describing a plan" in _PLANNER_CORE_CONTRACT
    assert "PROJECT_DONE=true|false" in _PLANNER_CORE_CONTRACT
    assert "not JSON" in _PLANNER_CORE_CONTRACT


class _Runner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                "Changed the implementation.\n"
                "PROJECT_DONE=true\n"
                "REASON=Focused verification passed."
            ],
        )


class _SequenceRunner:
    def __init__(self, messages: list[str]) -> None:
        self.messages = list(messages)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.messages.pop(0)])


def test_plan_next_disables_schema_and_planner_timeouts(monkeypatch) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(
            working_dir="/tmp/project",
            dangerous_yolo=True,
        ),
    )

    assert verdict.project_done is True
    call = runner.calls[0]
    assert call["run_label"] == "planner.cycle0"
    options = call["options"]
    assert options.output_schema_path is None
    assert options.external_interrupt_reason_provider is None
    assert options.watchdog_hard_idle_seconds == 0
    assert options.dangerous_yolo is True


def test_plan_next_repairs_not_done_empty_task_response(monkeypatch) -> None:
    runner = _SequenceRunner([
        "PROJECT_DONE=false\nREASON=implementation still needs a concrete follow-up",
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=queue the concrete verifier repair",
                "TASK_KEY=verifier",
                "TASK_TITLE=Repair verifier path",
                (
                    "TASK_OBJECTIVE=Update src/verifier.py and run pytest "
                    "tests/test_verifier.py."
                ),
                "TASK_ACCEPTANCE_CHECK=pytest tests/test_verifier.py",
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="fix the verifier",
        planning_cycle=7,
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.project_done is False
    assert [task.title for task in verdict.new_tasks] == ["Repair verifier path"]
    assert runner.calls[0]["run_label"] == "planner.cycle7"
    assert runner.calls[1]["run_label"] == "planner.cycle7.repair1"
    assert NO_CONCRETE_TASKS_ERROR in runner.calls[1]["prompt"]
    assert (
        "Never return `PROJECT_DONE=false` without either `WAITING=true`"
        in runner.calls[1]["prompt"]
    )
    assert runner.calls[1]["options"].working_dir == "/tmp/project"


def test_plan_next_reports_bounded_failure_after_empty_task_repair_exhaustion(
    monkeypatch,
) -> None:
    runner = _SequenceRunner([
        "PROJECT_DONE=false\nREASON=still not complete",
        "PROJECT_DONE=false\nREASON=still no concrete task",
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="fix the verifier",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.project_done is False
    assert verdict.new_tasks == []
    assert verdict.error.startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in verdict.error
    assert len(runner.calls) == 2
