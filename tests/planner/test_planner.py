from __future__ import annotations

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.planner.planner import (
    NO_CONCRETE_TASKS_ERROR,
    Planner,
    PlannerConfig,
    TaskSpec,
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


def test_task_spec_preserves_legacy_positional_fields() -> None:
    task = TaskSpec(
        "Task",
        "Objective",
        3,
        "chemistry",
        "evidence",
        "pytest -q",
        ["do not edit formal state"],
        [{"kind": "artifact", "ref": "RESULT.md"}],
        "final_submission",
        True,
        "local-key",
        ["parent-key"],
        "authorization-id",
        "authorization-action",
    )

    assert task.acceptance_check == "pytest -q"
    assert task.non_goals == ["do not edit formal state"]
    assert task.context_refs == [{"kind": "artifact", "ref": "RESULT.md"}]
    assert task.scope == "final_submission"
    assert task.stage_closing is True
    assert task.key == "local-key"
    assert task.deps == ["parent-key"]
    assert task.authorization_id == "authorization-id"
    assert task.authorization_action == "authorization-action"
    assert task.require_independent_review is False


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


def test_parse_task_context_refs_into_rich_task_spec() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One bounded task remains.\n"
        "TASK_KEY=probe\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Probe candidate\n"
        "TASK_OBJECTIVE=Update the bounded candidate and validate it.\n"
        "TASK_ACCEPTANCE_CHECK=validator exits zero\n"
        "TASK_NON_GOALS=do not edit pipeline state|do not run physical experiments\n"
        "TASK_CONTEXT_REFS=artifact::research/chem_playground/x/QUESTION.md::question|"
        "artifact::research/chem_playground/x/RESULT.md::result\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=true\n"
        "TASK_SKIP_STAGE_TRANSITION=true"
    )

    assert verdict.error == ""
    task = verdict.new_tasks[0]
    assert task.acceptance_check == "validator exits zero"
    assert task.non_goals == [
        "do not edit pipeline state",
        "do not run physical experiments",
    ]
    assert [ref["ref"] for ref in task.context_refs] == [
        "research/chem_playground/x/QUESTION.md",
        "research/chem_playground/x/RESULT.md",
    ]
    assert task.stage_closing is False
    assert task.require_independent_review is True
    assert task.skip_stage_transition is True


def test_parse_rejects_malformed_task_metadata() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One task remains.\n"
        "TASK_KEY=probe\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Probe candidate\n"
        "TASK_OBJECTIVE=Run one probe.\n"
        "TASK_CONTEXT_REFS=not-a-ref\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=maybe\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false"
    )

    assert verdict.project_done is False
    assert verdict.new_tasks == []
    assert verdict.error.startswith("invalid planner task metadata:")


def test_parse_rejects_omitted_review_control_fields() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=Truncated task footer.\n"
        "TASK_KEY=probe\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Probe candidate\n"
        "TASK_OBJECTIVE=Run one probe."
    )

    assert verdict.new_tasks == []
    assert "missing required control fields" in verdict.error


def test_parse_rejects_entire_batch_when_one_task_lacks_objective() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One malformed sibling.\n"
        "TASK_KEY=broken\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Broken task\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false\n"
        "TASK_KEY=valid\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Valid task\n"
        "TASK_OBJECTIVE=Do valid work.\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false"
    )

    assert verdict.new_tasks == []
    assert "non-empty TASK_TITLE and TASK_OBJECTIVE" in verdict.error


def test_parse_rejects_stage_transition_skip_without_review_only_contract() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One task remains.\n"
        "TASK_KEY=probe\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Probe candidate\n"
        "TASK_OBJECTIVE=Run one probe.\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=true"
    )

    assert verdict.new_tasks == []
    assert "TASK_SKIP_STAGE_TRANSITION=true requires" in verdict.error


def test_parse_rejects_stage_transition_skip_for_final_submission() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One task remains.\n"
        "TASK_KEY=final\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Certify final submission\n"
        "TASK_OBJECTIVE=Review the final submission.\n"
        "TASK_SCOPE=final_submission\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=true\n"
        "TASK_SKIP_STAGE_TRANSITION=true"
    )

    assert verdict.new_tasks == []
    assert "TASK_SCOPE=bounded" in verdict.error


def test_parse_rejects_duplicate_task_keys() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=Ambiguous graph.\n"
        "TASK_KEY=parent\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=First parent\n"
        "TASK_OBJECTIVE=Do first work.\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false\n"
        "TASK_KEY=parent\n"
        "TASK_DEPS=\n"
        "TASK_TITLE=Second parent\n"
        "TASK_OBJECTIVE=Do second work.\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false\n"
        "TASK_KEY=child\n"
        "TASK_DEPS=parent\n"
        "TASK_TITLE=Child\n"
        "TASK_OBJECTIVE=Consume parent output.\n"
        "TASK_SCOPE=bounded\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
        "TASK_SKIP_STAGE_TRANSITION=false"
    )

    assert verdict.new_tasks == []
    assert "TASK_KEY values must be unique" in verdict.error


@pytest.mark.parametrize(
    "payload",
    [
        (
            "PROJECT_DONE=true\n"
            "WAITING=true\n"
            "WAITING_REASON=Contradictory completion."
        ),
        (
            "PROJECT_DONE=false\n"
            "WAITING=true\n"
            "WAITING_REASON=Contradictory task.\n"
            "TASK_KEY=probe\n"
            "TASK_DEPS=\n"
            "TASK_TITLE=Probe candidate\n"
            "TASK_OBJECTIVE=Run one probe.\n"
            "TASK_SCOPE=bounded\n"
            "TASK_STAGE_CLOSING=false\n"
            "TASK_REQUIRE_INDEPENDENT_REVIEW=false\n"
            "TASK_SKIP_STAGE_TRANSITION=false"
        ),
    ],
)
def test_parse_rejects_waiting_with_completion_or_tasks(payload: str) -> None:
    verdict = parse_planner_text(payload)

    assert verdict.project_done is False
    assert verdict.waiting is False
    assert verdict.new_tasks == []
    assert verdict.error == (
        "planner waiting marker conflicts with completion or task blocks"
    )


def test_missing_completion_marker_is_retryable() -> None:
    verdict = parse_planner_text("I inspected the repository but did not finish.")

    assert verdict.project_done is False
    assert verdict.error == "planner missing key-value completion marker"


def test_planner_prompt_requires_direct_edits_and_plain_key_values() -> None:
    assert "direct project executor" in _PLANNER_CORE_CONTRACT
    assert "edit project files" in _PLANNER_CORE_CONTRACT
    assert "Do not stop after describing a plan" in _PLANNER_CORE_CONTRACT
    assert "PROJECT_DONE=true" in _PLANNER_CORE_CONTRACT
    assert "PROJECT_DONE=false" in _PLANNER_CORE_CONTRACT
    assert "not JSON" in _PLANNER_CORE_CONTRACT
    assert "TASK_CONTEXT_REFS" in _PLANNER_CORE_CONTRACT
    assert "TASK_STAGE_CLOSING" in _PLANNER_CORE_CONTRACT
    assert "TASK_REQUIRE_INDEPENDENT_REVIEW" in _PLANNER_CORE_CONTRACT
    assert "TASK_SKIP_STAGE_TRANSITION" in _PLANNER_CORE_CONTRACT


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
                "TASK_SCOPE=bounded",
                "TASK_STAGE_CLOSING=false",
                "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
                "TASK_SKIP_STAGE_TRANSITION=false",
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
