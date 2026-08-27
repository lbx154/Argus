from __future__ import annotations

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.planner.planner import (
    NO_CONCRETE_TASKS_ERROR,
    OPEN_ENDED_PROJECT_DONE_ERROR,
    PLANNER_SUPERSEDED_ERROR,
    Planner,
    PlannerConfig,
    parse_planner_payload,
    parse_planner_text,
    parse_task_scope,
)
from argus_skill.planner.work_kind import DEFAULT_WORK_KIND, WORK_KINDS
from argus_skill.roles.prompts.planner import (
    _BOUNDED_DAG_FOOTER,
    _PLANNER_CORE_CONTRACT,
    _PLANNER_DECISION_FOOTER,
)


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


def test_planner_uses_only_the_explicit_final_footer() -> None:
    verdict = parse_planner_text(
        "A quoted example says PROJECT_DONE=true, but work remains.\n"
        "TASK_TITLE=Discarded thought\n"
        "Decision:\n"
        "PROJECT_DONE=false\n"
        "REASON=Run the real benchmark.\n"
        "TASK_KEY=benchmark\n"
        "TASK_TITLE=Run benchmark\n"
        "TASK_OBJECTIVE=Execute the official harness."
    )

    assert verdict.project_done is False
    assert [task.title for task in verdict.new_tasks] == ["Run benchmark"]


def test_structured_planner_payload_preserves_list_item_text() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "one task remains",
        "tasks": [{
            "key": "repair",
            "deps": [],
            "title": "Repair the parser",
            "objective": "Preserve structured fields.",
            "hypothesis": "The parser drops measured feedback.",
            "goal_contribution": "Keep the proposer loop evidence-driven.",
            "expected_regressions": "Legacy text may need a compatibility path.",
            "decision_rule": "Revise the parser if feedback fields still disappear.",
            "scope": "bounded",
            "non_goals": ["Do not rewrite A | B."],
            "owns_paths": ["tests/a,b.txt"],
        }],
    })

    assert verdict.error == ""
    assert verdict.new_tasks[0].non_goals == ["Do not rewrite A | B."]
    assert verdict.new_tasks[0].owns_paths == ["tests/a,b.txt"]
    assert verdict.new_tasks[0].hypothesis == (
        "The parser drops measured feedback."
    )
    assert verdict.new_tasks[0].decision_rule.startswith("Revise the parser")


@pytest.mark.parametrize("work_kind", WORK_KINDS)
def test_structured_planner_payload_accepts_explicit_work_kind(work_kind: str) -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "one typed task remains",
        "tasks": [{
            "title": "Typed task",
            "objective": "Execute the bounded task.",
            "scope": "bounded",
            "work_kind": work_kind,
        }],
    })

    assert verdict.error == ""
    assert verdict.new_tasks[0].work_kind == work_kind


def test_structured_planner_payload_defaults_unknown_work_kind() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "invalid type",
        "tasks": [{
            "title": "Invented type",
            "objective": "Deliver and validate an algorithm.",
            "scope": "bounded",
            "work_kind": "research_magic",
        }],
    })

    assert verdict.error == ""
    assert verdict.new_tasks[0].work_kind == DEFAULT_WORK_KIND
    assert verdict.diagnostics == (
        "task 1 unsupported work_kind defaulted to scope",
    )


def test_missing_work_kind_uses_legacy_default_without_prose_inference() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "legacy task",
        "tasks": [{
            "title": "Deliver the optimized environment",
            "objective": "Validate the algorithm and ship it.",
            "scope": "bounded",
        }],
    })

    assert verdict.error == ""
    assert verdict.new_tasks[0].work_kind == DEFAULT_WORK_KIND


def test_structured_planner_wait_preserves_framed_lists() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "the external job is still running",
        "waiting": {
            "blocker_fingerprint": "job-running",
            "recheck_condition": "the durable job reaches terminal state",
            "recheck_token": "job-42",
            "wait_mode": "event",
            "wake_on": ["subagent_state"],
            "watched_paths": ["results/a,b.json"],
            "wait_id": "job-42",
        },
        "tasks": [],
    })

    assert verdict.error == ""
    assert verdict.waiting_contract is not None
    assert verdict.waiting_contract.wake_on == ("subagent_state",)
    assert verdict.waiting_contract.watched_paths == ("results/a,b.json",)
    assert verdict.waiting_contract.wait_id == "job-42"


def test_structured_planner_payload_rejects_wrong_field_types() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "invalid task framing",
        "tasks": "not-an-array",
    })

    assert verdict.new_tasks == []
    assert verdict.error == (
        "invalid structured planner decision: tasks must be an array"
    )


def test_parse_planner_task_carries_feedback_contract_but_not_legacy_workdir() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=work in the cloned target repository",
            "TASK_KEY=target",
            "TASK_TITLE=Repair target kernel",
            "TASK_OBJECTIVE=Edit and test the target kernel.",
            "TASK_HYPOTHESIS=The target kernel contains the defect.",
            "TASK_GOAL_CONTRIBUTION=Fix the operator's target repository.",
            "TASK_EXPECTED_REGRESSIONS=The focused test may stay red during repair.",
            "TASK_DECISION_RULE=Replan if the defect is outside this repository.",
            "TASK_WORK_KIND=engineering_optimization",
            "TASK_WORKDIR=flash-linear-attention",
            "TASK_ACCEPTANCE_CHECK=pytest tests/ops/test_attnres.py -q",
        ])
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].execution_workdir == ""
    assert verdict.new_tasks[0].hypothesis == (
        "The target kernel contains the defect."
    )
    assert verdict.new_tasks[0].goal_contribution.startswith(
        "Fix the operator"
    )
    assert verdict.new_tasks[0].decision_rule.startswith(
        "Replan if the defect"
    )
    assert verdict.new_tasks[0].acceptance_check == (
        "pytest tests/ops/test_attnres.py -q"
    )
    assert verdict.new_tasks[0].work_kind == "engineering_optimization"


def test_parse_planner_emits_disjoint_parallel_task_batch() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=two independent evidence tracks remain",
            "TASK_KEY=route-a",
            "TASK_DEPS=",
            "TASK_TITLE=Investigate route A",
            "TASK_OBJECTIVE=Write route A.",
            "TASK_PARALLEL_SAFE=true",
            "TASK_OWNS_PATHS=research/routes/a.md",
            "TASK_KEY=route-b",
            "TASK_DEPS=",
            "TASK_TITLE=Investigate route B",
            "TASK_OBJECTIVE=Write route B.",
            "TASK_PARALLEL_SAFE=true",
            "TASK_OWNS_PATHS=research/routes/b.md",
        ])
    )

    assert [task.key for task in verdict.new_tasks] == ["route-a", "route-b"]
    assert all(task.parallel_safe for task in verdict.new_tasks)
    assert [task.owns_paths for task in verdict.new_tasks] == [
        ["research/routes/a.md"],
        ["research/routes/b.md"],
    ]


def test_parse_status_summary_aliases() -> None:
    verdict = parse_planner_text(
        "STATUS=completed\nSUMMARY=Implementation and verification finished."
    )

    assert verdict.project_done is True
    assert verdict.reason == "Implementation and verification finished."


def test_parse_planner_task_ignores_legacy_blocker_fingerprint() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=The same external blocker remains.\n"
        "TASK_KEY=retry\n"
        "TASK_TITLE=Retry renamed task\n"
        "TASK_OBJECTIVE=Verify whether the blocker changed.\n"
        "TASK_BLOCKER_FINGERPRINT=dataset-license:benchmark-x"
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].blocker_fingerprint == ""


def test_parse_numbered_planner_task_fields() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=Delegate the next bounded frontier.\n"
        "TASK_1_TITLE=Certify the next multiplier family\n"
        "TASK_1_OBJECTIVE=Produce a Reviewer-checkable theorem or obstruction.\n"
        "TASK_1_ACCEPTANCE_CHECK=Run the exact verifier."
    )

    assert verdict.error == ""
    assert len(verdict.new_tasks) == 1
    assert verdict.new_tasks[0].title == "Certify the next multiplier family"
    assert verdict.new_tasks[0].objective == (
        "Produce a Reviewer-checkable theorem or obstruction."
    )
    assert verdict.new_tasks[0].acceptance_check == "Run the exact verifier."


def test_parse_multiple_numbered_planner_tasks() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "TASK_1_TITLE=First task\n"
        "TASK_1_OBJECTIVE=Do the first bounded task.\n"
        "TASK_2_TITLE=Second task\n"
        "TASK_2_OBJECTIVE=Do the dependent bounded task."
    )

    assert verdict.error == ""
    assert [task.title for task in verdict.new_tasks] == [
        "First task",
        "Second task",
    ]


def test_incomplete_key_value_result_is_retryable() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\nREASON=External credential is still required."
    )

    assert verdict.project_done is False
    assert verdict.error == "planner said not done but produced no concrete tasks"


def test_operator_wait_defaults_to_event_driven_authorization() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=An operator must approve the external action.\n"
        "WAITING=true\n"
        "BLOCKER_FINGERPRINT=external-approval\n"
        "RECHECK_CONDITION=Operator approval arrives.\n"
        "RECHECK_TOKEN=approval-pending\n"
        "OPERATOR_ACTION_REQUIRED=true"
    )

    assert verdict.error == ""
    assert verdict.waiting_contract is not None
    assert verdict.waiting_contract.operator_action_required is True
    assert verdict.waiting_contract.wait_mode == "event"
    assert verdict.waiting_contract.wake_on == ("authorization",)


def test_wait_with_independent_task_preserves_both_and_marks_diagnostic() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One branch waits for operator input; an independent check can run.\n"
        "WAITING=true\n"
        "WAITING_REASON=Waiting for the operator answer.\n"
        "BLOCKER_FINGERPRINT=operator-choice\n"
        "RECHECK_CONDITION=Operator provides the choice.\n"
        "RECHECK_TOKEN=choice-v1\n"
        "OPERATOR_ACTION_REQUIRED=true\n"
        "TASK_KEY=independent-check\n"
        "TASK_TITLE=Run independent check\n"
        "TASK_OBJECTIVE=Collect evidence that does not depend on the operator choice."
    )

    assert verdict.error == ""
    assert verdict.waiting is True
    assert verdict.waiting_reason == "Waiting for the operator answer."
    assert [task.key for task in verdict.new_tasks] == ["independent-check"]
    assert any("preserving both" in item for item in verdict.diagnostics)


def test_missing_completion_marker_is_retryable() -> None:
    verdict = parse_planner_text("I inspected the repository but did not finish.")

    assert verdict.project_done is False
    assert verdict.error == "planner missing key-value completion marker"


@pytest.mark.parametrize(
    "text",
    [
        (
            '{"project_done":false,"reason":"grounded","tasks":['
            '{"key":"k1","deps":[],"title":"Implement it",'
            '"objective":"Implement the grounded task.","scope":"bounded"}]}'
        ),
        (
            "```json\n"
            '{"role":"planner","payload":{"project_done":false,'
            '"reason":"grounded","tasks":[{"key":"k1","deps":[],'
            '"title":"Implement it","objective":"Implement the grounded task.",'
            '"scope":"bounded"}]}}\n'
            "```\nBrief operator explanation."
        ),
    ],
)
def test_planner_accepts_json_decision_without_event_prefix(text: str) -> None:
    verdict = parse_planner_text(text)

    assert verdict.error == ""
    assert verdict.reason == "grounded"
    assert [task.title for task in verdict.new_tasks] == ["Implement it"]
    assert verdict.raw_text == text


def test_planner_prompt_requires_read_only_delegation_and_minimal_footer() -> None:
    assert "Planner read-only delegation contract" in _PLANNER_CORE_CONTRACT
    assert "Do not edit project files" in _PLANNER_CORE_CONTRACT
    assert "Engineer owns edits" in _PLANNER_CORE_CONTRACT
    assert "ARGUS_ROLE_DECISION=" not in _PLANNER_CORE_CONTRACT
    assert "PROJECT_DONE=false" in _PLANNER_CORE_CONTRACT
    assert "TASK_KEY=k1" in _PLANNER_CORE_CONTRACT
    assert "`wake_on`" in _PLANNER_CORE_CONTRACT
    assert "semantically" in _PLANNER_CORE_CONTRACT
    assert "synonyms/combined sources" in _PLANNER_CORE_CONTRACT
    assert "bounded poll" in _PLANNER_CORE_CONTRACT
    for field in ("`TASK_TITLE`", "`TASK_OBJECTIVE`", "`TASK_ACCEPTANCE_CHECK`"):
        assert field in _PLANNER_CORE_CONTRACT
    for field in (
        "TASK_WORKDIR",
        "TASK_CONTEXT_REFS",
        "TASK_REQUIRE_INDEPENDENT_REVIEW",
        "TASK_STAGE_CLOSING",
    ):
        assert field not in _PLANNER_CORE_CONTRACT
    assert "`TASK_SCOPE`" in _PLANNER_CORE_CONTRACT
    assert "optional (default `bounded`)" in _PLANNER_CORE_CONTRACT
    assert "optional (omit to hold)" in _PLANNER_CORE_CONTRACT
    assert "enqueue-time validation/normalization" not in _PLANNER_CORE_CONTRACT
    assert "external algorithm" in _PLANNER_CORE_CONTRACT
    assert "primary-source grounding" in _PLANNER_CORE_CONTRACT
    assert "starting context, not a" in _PLANNER_CORE_CONTRACT
    assert "fresh paper/source/issue/hardware investigation" in _PLANNER_CORE_CONTRACT
    assert "When related attempts repeatedly fail" in (
        _PLANNER_CORE_CONTRACT
    )
    assert "official implementations" in _PLANNER_CORE_CONTRACT


@pytest.mark.parametrize(
    "footer",
    [_PLANNER_DECISION_FOOTER, _BOUNDED_DAG_FOOTER],
)
def test_planner_footer_example_is_an_ambitious_experiment_program(
    footer: str,
) -> None:
    assert (
        "TASK_TITLE=Launch the strongest untested attack on the core hypothesis"
        in footer
    )
    assert (
        "TASK_OBJECTIVE=design and run the experiment whose outcome most changes "
        "what we believe, with success and failure criteria stated in advance"
        in footer
    )
    assert "TASK_TITLE=Run the next decisive check" not in footer


def test_planner_forbids_binary_outcome_labels_and_standing_keeps_exploring(
    tmp_path,
) -> None:
    finite = Planner._build_planner_prompt(
        continuous_objective=(
            "Promote the route if it wins; otherwise reject it with decisive evidence."
        ),
        journal_tail="The route was measured and rejected.",
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=False,
    )
    standing = Planner._build_planner_prompt(
        continuous_objective="Keep exploring new optimization mechanisms.",
        journal_tail="The current route was measured and rejected.",
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=True,
    )

    assert "accepted " + "no" + "-go" not in finite.lower()
    assert "bare launch verdict" in finite.lower()
    assert "what happened" in finite
    assert "timing/profiling" in finite
    assert "This campaign remains active until the operator stops it" not in finite
    assert "This campaign remains active until the operator stops it" in standing


def test_parse_planner_allows_binary_outcome_words_inside_a_task() -> None:
    forbidden_label = "no" + "-go"
    verdict = parse_planner_text(
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=The current route needs a replacement.",
                "TASK_TITLE=Measure the replacement route",
                "TASK_OBJECTIVE=Profile the runtime and test a controlled alternative.",
                (
                    "TASK_ACCEPTANCE_CHECK=Write a documented "
                    f"{forbidden_label} if it misses the threshold."
                ),
            ]
        )
    )

    assert verdict.project_done is False
    assert [task.title for task in verdict.new_tasks] == [
        "Measure the replacement route"
    ]
    assert verdict.error == ""


def test_parse_task_scope_accepts_final_certification_annotation() -> None:
    assert parse_task_scope("bounded — one coherent mission") == "bounded"
    assert parse_task_scope("final_submission (certification)") == "final_submission"
    assert parse_task_scope("a bounded slice of the work") == "bounded"


def test_structured_planner_skips_only_task_missing_required_content() -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "one usable task remains",
        "tasks": [
            {"title": "Incomplete row", "scope": "bounded"},
            {
                "key": "usable task",
                "deps": ["usable task"],
                "title": "Usable row",
                "objective": "Execute the understood work.",
                "scope": "an informal bounded description",
            },
        ],
    })

    assert verdict.error == ""
    assert [task.title for task in verdict.new_tasks] == ["Usable row"]
    assert verdict.new_tasks[0].scope == "bounded"
    assert verdict.new_tasks[0].key == verdict.new_tasks[0].deps[0]
    assert any("task 1 skipped" in item for item in verdict.diagnostics)
    assert any("unsupported scope" in item for item in verdict.diagnostics)


def test_planner_defaults_scope_when_final_submission_is_only_in_prose() -> None:
    verdict = parse_planner_text(
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=final certification still needs a host-visible mission",
                "TASK_KEY=final-certification",
                "TASK_TITLE=Make final certification host-visible",
                (
                    "TASK_OBJECTIVE=Run the certification handoff with "
                    "TASK_SCOPE=final_submission so the completion gate can consume it."
                ),
                "TASK_ACCEPTANCE_CHECK=Reviewer PASS is recorded.",
            ]
        )
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].scope == "bounded"
    assert "task 1 scope defaulted to bounded" in verdict.diagnostics


def test_parse_planner_task_sanitizes_malformed_dependency_controls() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=queue the grounded implementation",
            "TASK_KEY=grounded",
            (
                "TASK_DEPS=No external dependency. Preserve the dense baseline; "
                "do not repeat rejected work."
            ),
            "TASK_TITLE=Implement grounded method",
            "TASK_OBJECTIVE=Implement the source-backed method.",
            "TASK_SCOPE=bounded — one coherent mission",
        ])
    )

    assert verdict.error == ""
    assert len(verdict.new_tasks) == 1
    assert verdict.new_tasks[0].deps[0].startswith("No-external-dependency")
    assert "task 1 dependency identifiers normalized" in verdict.diagnostics


def test_plan_update_markdown_cannot_impersonate_later_footer_fields() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=run the real task",
            "TASK_KEY=real",
            "TASK_TITLE=Run the discriminating experiment",
            "TASK_OBJECTIVE=Measure the mechanism under intervention.",
            "TASK_SCOPE=bounded",
            "PLAN_UPDATE=# Research plan",
            "Objective: settle the mechanism.",
            "## Central hypotheses",
            "1. [untested] H1 — evidence: none",
            "## Experiment program",
            "TASK_KEY=markdown-not-a-task",
            "TASK_TITLE=This is evidence text, not another footer record",
            "## Established results",
            "PROJECT_DONE=true",
            "## Dead ends",
            "- none",
            "## Next milestone",
            "A replicated effect.",
        ])
    )

    assert verdict.project_done is False
    assert verdict.error == ""
    assert [task.key for task in verdict.new_tasks] == ["real"]


def test_parse_planner_task_treats_none_dependency_as_empty() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=queue the next independent route search",
            "TASK_KEY=route-search",
            "TASK_DEPS=none",
            "TASK_TITLE=Search the next route",
            "TASK_OBJECTIVE=Find a source-grounded candidate.",
        ])
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].deps == []


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
        return RunnerResult(
            exit_code=0,
            agent_messages=[self.messages.pop(0)],
            thread_id="planner-thread",
        )


def test_plan_next_disables_schema_and_forces_read_only_tools(monkeypatch) -> None:
    runner = _Runner()

    def interrupt() -> None:
        return None
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(
            working_dir="/tmp/project",
            add_dirs=["/tmp/project-state"],
            dangerous_yolo=True,
            external_interrupt_reason_provider=interrupt,
        ),
    )

    assert verdict.project_done is True
    call = runner.calls[0]
    assert call["run_label"] == "planner.cycle0"
    options = call["options"]
    assert not hasattr(options, "output_schema_path")
    assert not hasattr(options, "_argus_tool_call_observer")
    assert not hasattr(PlannerConfig(), "grounding_max_tool_calls")
    assert not hasattr(PlannerConfig(), "grounding_max_seconds")
    assert options.external_interrupt_reason_provider is interrupt
    # None delegates liveness to the shared backend default; zero disables the
    # watchdog and lets a silent Planner call block a campaign indefinitely.
    assert options.watchdog_hard_idle_seconds is None
    assert options.dangerous_yolo is False
    assert options.full_auto is False
    assert options.sandbox_mode == "read-only"
    assert options.add_dirs == ["/tmp/project-state"]


def test_plan_next_defaults_to_read_only_tool_access(monkeypatch) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    Planner(runner).plan_next(
        continuous_objective="inspect safely",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    options = runner.calls[0]["options"]
    assert options.dangerous_yolo is False
    assert options.full_auto is False
    assert options.sandbox_mode == "read-only"


def test_plan_next_uses_process_decision_without_final_message(monkeypatch) -> None:
    class _DecisionRunner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                agent_messages=[],
                role_decisions=[{
                    "role": "planner",
                    "payload": {
                        "project_done": False,
                        "reason": "one bounded repair remains",
                        "tasks": [{
                            "key": "repair",
                            "deps": [],
                            "title": "Repair the parser",
                            "objective": "Fix the parser and run its focused test.",
                            "scope": "bounded",
                        }, {
                            "key": "verify",
                            "deps": ["repair"],
                            "title": "Verify the repair",
                            "objective": "Run the integration check.",
                            "scope": "bounded",
                            "vertical": "argus_maintenance",
                        }],
                    },
                }],
                thread_id="planner-thread",
            )

    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    verdict = Planner(_DecisionRunner()).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].key == "repair"
    assert verdict.new_tasks[1].deps == ["repair"]
    assert verdict.new_tasks[1].vertical == "argus_maintenance"


def test_planner_decision_event_preserves_task_scope(monkeypatch) -> None:
    class _MissingScopeRunner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                agent_messages=[],
                role_decisions=[{
                    "role": "planner",
                    "payload": {
                        "project_done": False,
                        "reason": "final certification remains",
                        "tasks": [{
                            "key": "final-certification",
                            "deps": [],
                            "title": "Obtain final certification",
                            "objective": "Run the final independent Reviewer gate.",
                        }],
                    },
                }],
            )

    class _DecisionRunner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                agent_messages=[],
                role_decisions=[{
                    "role": "planner",
                    "payload": {
                        "project_done": False,
                        "reason": "final certification remains",
                        "tasks": [{
                            "key": "final-certification",
                            "deps": [],
                            "title": "Obtain final certification",
                            "objective": "Run the final independent Reviewer gate.",
                            "scope": "final_submission",
                        }],
                    },
                }],
                thread_id="planner-thread",
            )

    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    defaulted = Planner(_MissingScopeRunner()).plan_next(
        continuous_objective="certify final submission",
        config=PlannerConfig(working_dir="/tmp/project"),
    )
    assert defaulted.error == ""
    assert defaulted.new_tasks[0].scope == "bounded"
    assert "task 1 scope defaulted to bounded" in defaulted.diagnostics

    verdict = Planner(_DecisionRunner()).plan_next(
        continuous_objective="certify final submission",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].scope == "final_submission"


def test_parse_planner_task_vertical() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=route this node to the maintenance role\n"
        "TASK_KEY=repair\n"
        "TASK_TITLE=Repair Argus\n"
        "TASK_OBJECTIVE=Fix the lifecycle bug.\n"
        "TASK_VERTICAL=argus_maintenance"
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].vertical == "argus_maintenance"


def test_parse_planner_structured_stage_advance() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=the next task is a real benchmark\n"
        "ADVANCE_TO_STAGE=benchmark\n"
        "TASK_KEY=benchmark\n"
        "TASK_TITLE=Run benchmark\n"
        "TASK_OBJECTIVE=Execute the real benchmark."
    )

    assert verdict.error == ""
    assert verdict.advance_to_stage == "benchmark"
    assert verdict.new_tasks[0].key == "benchmark"


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
                "TASK_HYPOTHESIS=The verifier path is the remaining defect.",
                "TASK_GOAL_CONTRIBUTION=Restore trustworthy verification for the objective.",
                "TASK_EXPECTED_REGRESSIONS=The focused verifier may stay red during repair.",
                "TASK_DECISION_RULE=Replan if the failing evidence points outside this path.",
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
    assert verdict.new_tasks[0].hypothesis == (
        "The verifier path is the remaining defect."
    )
    assert verdict.new_tasks[0].goal_contribution.startswith(
        "Restore trustworthy verification"
    )
    assert verdict.new_tasks[0].decision_rule.startswith(
        "Replan if the failing evidence"
    )
    assert runner.calls[0]["run_label"] == "planner.cycle7"
    assert runner.calls[1]["run_label"] == "planner.cycle7.repair1"
    assert runner.calls[1]["resume_thread_id"] == "planner-thread"
    assert "Original Planner prompt" not in runner.calls[1]["prompt"]
    assert "Do not use tools" in runner.calls[1]["prompt"]
    assert NO_CONCRETE_TASKS_ERROR in runner.calls[1]["prompt"]
    assert "PROJECT_DONE=false" in runner.calls[1]["prompt"]
    assert "If work remains, include concrete tasks" in runner.calls[1]["prompt"]
    assert "TASK_TITLE=Run the next decisive check" in runner.calls[1]["prompt"]
    assert (
        "TASK_OBJECTIVE=execute the concrete check required by current evidence"
        in runner.calls[1]["prompt"]
    )
    assert runner.calls[1]["options"].working_dir == "/tmp/project"


def test_plan_next_accepts_structured_decision_with_redundant_brace(monkeypatch) -> None:
    runner = _SequenceRunner([
        (
            'ARGUS_ROLE_DECISION={"role":"planner","payload":'
            '{"project_done":false,"reason":"one task remains",'
            '"tasks":[{"title":"Run the benchmark",'
            '"objective":"Measure decode throughput.","scope":"bounded"}]}}}'
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="serve the full model",
        planning_cycle=3,
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert [task.title for task in verdict.new_tasks] == ["Run the benchmark"]
    assert len(runner.calls) == 1


def test_plan_next_sanitizes_invalid_dependency_identifier(monkeypatch) -> None:
    runner = _SequenceRunner([
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=queue the selected implementation",
                "TASK_KEY=ri<REDACTED:openai-key>",
                "TASK_TITLE=Implement the selected method",
                "TASK_OBJECTIVE=Build the first working method prototype.",
            ]
        ),
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=queue the selected implementation",
                "TASK_KEY=risk-kv-offline-evaluator",
                "TASK_TITLE=Implement the selected method",
                "TASK_OBJECTIVE=Build the first working method prototype.",
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="build the selected method",
        planning_cycle=8,
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].key.startswith("ri-REDACTED:openai-key-")
    assert len(runner.calls) == 1
    assert "task 1 dependency identifiers normalized" in verdict.diagnostics


def test_plan_next_holds_stage_when_staged_advance_is_missing(monkeypatch) -> None:
    runner = _SequenceRunner([
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=run the benchmark",
                "TASK_KEY=benchmark",
                "TASK_TITLE=Run benchmark",
                "TASK_OBJECTIVE=Execute the real benchmark.",
            ]
        ),
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=run the benchmark",
                "ADVANCE_TO_STAGE=benchmark",
                "TASK_KEY=benchmark",
                "TASK_TITLE=Run benchmark",
                "TASK_OBJECTIVE=Execute the real benchmark.",
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="produce the paper",
        planning_cycle=9,
        config=PlannerConfig(
            working_dir="/tmp/project",
            require_stage_decision=True,
            current_stage="plan",
        ),
    )

    assert verdict.error == ""
    assert verdict.advance_to_stage == ""
    assert len(runner.calls) == 1
    assert "advance_to_stage missing; holding current stage" in verdict.diagnostics


def test_plan_next_keeps_task_containing_binary_outcome_label(monkeypatch) -> None:
    forbidden_label = "no" + "-go"
    runner = _SequenceRunner([
        "\n".join(
            [
                "PROJECT_DONE=false",
                f"REASON=The route is a {forbidden_label}.",
                "TASK_TITLE=Replace the route",
                "TASK_OBJECTIVE=Implement the presumed replacement.",
            ]
        ),
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=The current measurement is inconclusive.",
                "TASK_TITLE=Attribute the runtime",
                (
                    "TASK_OBJECTIVE=Inspect the hot path and live waits, then run "
                    "phase timing or a controlled comparison."
                ),
                (
                    "TASK_ACCEPTANCE_CHECK=Evidence explains a material share of "
                    "elapsed time or states that the cause is still unclear."
                ),
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="diagnose the slow route",
        planning_cycle=4,
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert [task.title for task in verdict.new_tasks] == ["Replace the route"]
    assert len(runner.calls) == 1


def test_plan_next_downgrades_invalid_skip_hint_without_repair_call(
    monkeypatch,
) -> None:
    runner = _SequenceRunner([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=delegate the missing scope deliverable",
            "TASK_KEY=scope",
            "TASK_TITLE=Complete kernel campaign scope",
            "TASK_OBJECTIVE=Create the missing scope artifacts without editing kernels.",
            "TASK_HYPOTHESIS=A bounded scope pass can unlock discovery.",
            "TASK_GOAL_CONTRIBUTION=Advance the campaign from scope to discovery.",
            "TASK_EXPECTED_REGRESSIONS=None; production code is read-only.",
            "TASK_DECISION_RULE=Stop if the target worktree is not clean main.",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
            "TASK_SKIP_STAGE_TRANSITION=true",
            "TASK_ACCEPTANCE_CHECK=scope completion hook reports no issues",
        ])
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="run the kernel campaign",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert [task.title for task in verdict.new_tasks] == [
        "Complete kernel campaign scope"
    ]
    assert verdict.new_tasks[0].skip_stage_transition is False
    assert len(runner.calls) == 1


def test_plan_next_accepts_minimal_task_without_mission_quality_fields(
    monkeypatch,
) -> None:
    runner = _SequenceRunner([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=try the next checker",
            "TASK_KEY=weak",
            "TASK_TITLE=Make checker green",
            "TASK_OBJECTIVE=Change code until the local checker passes.",
            "TASK_ACCEPTANCE_CHECK=pytest tests/test_checker.py",
        ]),
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=ground the checker repair in the user goal",
            "TASK_KEY=grounded",
            "TASK_TITLE=Repair the user-visible parser behavior",
            "TASK_OBJECTIVE=Fix the parser defect and verify the user-visible case.",
            "TASK_HYPOTHESIS=The parser branch drops the required user-visible value.",
            "TASK_GOAL_CONTRIBUTION=Restore the behavior requested by the user.",
            "TASK_EXPECTED_REGRESSIONS=The local checker may remain red during repair.",
            "TASK_DECISION_RULE=Replan if the parser branch is not causal.",
            "TASK_ACCEPTANCE_CHECK=Reproduce the user case, then run pytest tests/test_checker.py.",
        ]),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="restore parser behavior",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].title == "Make checker green"
    assert len(runner.calls) == 1


def test_plan_next_ignores_malformed_context_ref_metadata(monkeypatch) -> None:
    runner = _SequenceRunner([
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=summarize the supplied paper",
                "TASK_KEY=paper-summary",
                "TASK_TITLE=Summarize paper",
                "TASK_OBJECTIVE=Read the supplied paper and summarize it.",
                (
                    "TASK_CONTEXT_REFS=.argus/PIPELINE_STATE.json; "
                    "/tmp/runtime/events.jsonl"
                ),
            ]
        ),
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=summarize the supplied paper",
                "TASK_KEY=paper-summary",
                "TASK_TITLE=Summarize paper",
                "TASK_OBJECTIVE=Read the supplied paper and summarize it.",
                "TASK_HYPOTHESIS=The supplied paper can be summarized from current sources.",
                "TASK_GOAL_CONTRIBUTION=Produce the requested grounded paper summary.",
                "TASK_EXPECTED_REGRESSIONS=None expected; this is read-only synthesis.",
                "TASK_DECISION_RULE=Stop and ask for sources if the referenced paper is absent.",
                (
                    "TASK_CONTEXT_REFS=artifact::.argus/PIPELINE_STATE.json::"
                    "current stage"
                ),
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="summarize the paper",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].context_refs == []
    assert len(runner.calls) == 1


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


def test_open_ended_planner_must_delegate_after_one_increment() -> None:
    runner = _SequenceRunner([
        "PROJECT_DONE=true\nREASON=finished one cache optimization",
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=continue the standing optimization campaign",
            "TASK_KEY=next",
            "TASK_TITLE=Remove duplicate Manager reply rows",
            "TASK_OBJECTIVE=Unify live and persisted Manager message identity.",
            "TASK_HYPOTHESIS=Identity drift creates duplicate conversation rows.",
            "TASK_GOAL_CONTRIBUTION=Keep the standing user conversation coherent.",
            "TASK_EXPECTED_REGRESSIONS=Replay ordering may move while identity is unified.",
            "TASK_DECISION_RULE=Replan if duplicate rows survive stable message ids.",
            "TASK_ACCEPTANCE_CHECK=run the focused TUI stream tests",
        ]),
    ])

    verdict = Planner(runner).plan_next(
        continuous_objective="keep optimizing Argus",
        config=PlannerConfig(working_dir="/tmp/project", open_ended=True),
    )

    assert verdict.project_done is False
    assert [task.title for task in verdict.new_tasks] == [
        "Remove duplicate Manager reply rows"
    ]
    assert "TASK_STAGE_CLOSING" not in runner.calls[0]["prompt"]
    assert "TASK_REQUIRE_INDEPENDENT_REVIEW" not in runner.calls[0]["prompt"]
    assert OPEN_ENDED_PROJECT_DONE_ERROR in runner.calls[1]["prompt"]


def test_planner_reports_newer_operator_generation_as_superseded() -> None:
    class Runner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=1,
                fatal_error=f"External interrupt: {PLANNER_SUPERSEDED_ERROR}",
            )

    verdict = Planner(Runner()).plan_next(
        continuous_objective="keep optimizing Argus",
        config=PlannerConfig(working_dir="/tmp/project", open_ended=True),
    )

    assert verdict.project_done is False
    assert verdict.error == PLANNER_SUPERSEDED_ERROR
