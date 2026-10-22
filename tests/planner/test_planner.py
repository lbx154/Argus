from __future__ import annotations

import pytest

from argus.core.models import RunnerResult
from argus.planner.planner import (
    NO_CONCRETE_TASKS_ERROR,
    OPEN_ENDED_PROJECT_DONE_ERROR,
    PLANNER_SUPERSEDED_ERROR,
    Planner,
    PlannerConfig,
    parse_planner_payload,
    parse_planner_text,
    parse_task_scope,
)
from argus.roles.prompts.planner import (
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


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (
            "RETIRE_TASK=item-a | The experiment refuted the hypothesis.",
            (("item-a", "The experiment refuted the hypothesis."),),
        ),
        (
            "RETIRE_TASK=item-a | The experiment refuted the hypothesis.\n"
            "RETIRE_TASK=item-b | This repair belongs to the same closed family.",
            (
                ("item-a", "The experiment refuted the hypothesis."),
                ("item-b", "This repair belongs to the same closed family."),
            ),
        ),
    ],
)
def test_parse_retire_tasks(lines: str, expected: tuple) -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\nREASON=Retire the refuted line of work.\n" + lines
    )

    assert verdict.error == ""
    assert verdict.new_tasks == []
    assert verdict.retire_tasks == expected


@pytest.mark.parametrize(
    "line",
    ["RETIRE_TASK=item-a", "RETIRE_TASK=item-a | ", "RETIRE_TASK= | No item.", "RETIRE_TASK="],
)
def test_parse_retire_task_ignores_malformed_line(line: str) -> None:
    verdict = parse_planner_text("PROJECT_DONE=true\nREASON=Work is complete.\n" + line)

    assert verdict.error == ""
    assert verdict.retire_tasks == ()


@pytest.mark.parametrize("line", ["→ manager", "→ done"])
def test_completion_handed_to_the_manager_is_completion(line: str) -> None:
    """2026-10-20: `PROJECT_DONE=true` closed by `→ manager` was refused as a
    conflict twice and stopped the daemon on a planner error."""
    verdict = parse_planner_text(
        "Decision:\nPROJECT_DONE=true\nREASON=The chosen behaviour is reviewed and delivered.\n" + line
    )

    assert verdict.error == ""
    assert verdict.project_done is True and verdict.handoff == ""


def test_retire_tasks_use_only_the_footer_before_plan_update() -> None:
    verdict = parse_planner_text(
        "RETIRE_TASK=quoted | Discard this earlier thought.\n"
        "Decision:\nPROJECT_DONE=false\nREASON=Await new evidence.\n"
        "RETIRE_TASK=item-a | The hypothesis was refuted.\n"
        "→ engineer\n"
        "PLAN_UPDATE=# Research plan\n"
        "RETIRE_TASK=plan-text | This is document content."
    )

    assert verdict.error == ""
    assert verdict.handoff == "engineer"
    assert verdict.retire_tasks == (("item-a", "The hypothesis was refuted."),)


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ("SETTLE_INSTRUCTION=7", (7,)),
        ("SETTLE_INSTRUCTION=7\nSETTLE_INSTRUCTION=revision 9", (7, 9)),
        ("SETTLE_INSTRUCTION=7 | 9, 7", (7, 9)),
        ("SETTLE_INSTRUCTION=12 carried out by task k1", (12,)),
        ("SETTLE_INSTRUCTION=7 9", (7, 9)),
        ("SETTLE_INSTRUCTION=7 and revision 9", (7, 9)),
        ("SETTLE_INSTRUCTION=7 handled by the new task, 9 is still open", (7,)),
        ("SETTLE_INSTRUCTION=revision 12 is done", (12,)),
        ("SETTLE_INSTRUCTION=Revision: 7", (7,)),
        ("SETTLE_INSTRUCTION=#7; 9.", (7, 9)),
        ("SETTLE_INSTRUCTION=7 ②", (7,)),
        ("SETTLE_INSTRUCTION=②", ()),
        ("SETTLE_INSTRUCTION=the parser instruction", ()),
        ("SETTLE_INSTRUCTION=0", ()),
    ],
)
def test_parse_settled_instructions(lines: str, expected: tuple) -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\nREASON=Evaluate the parser.\n"
        "TASK_TITLE=Evaluate parser\nTASK_OBJECTIVE=Compare both parsers.\n" + lines
    )

    assert verdict.error == ""
    assert verdict.settled_instructions == expected
    assert [task.objective for task in verdict.new_tasks] == ["Compare both parsers."]


def test_long_settle_value_parses_in_linear_time() -> None:
    import time

    value = "   ".join(["1"] * 5000) + " done"
    started = time.monotonic()
    verdict = parse_planner_text(
        "PROJECT_DONE=false\nREASON=Evaluate the parser.\n"
        "TASK_TITLE=Evaluate parser\nTASK_OBJECTIVE=Compare both parsers.\n"
        f"SETTLE_INSTRUCTION={value}"
    )

    assert time.monotonic() - started < 1.0
    assert verdict.settled_instructions == (1,)


def test_settled_instructions_use_only_the_footer_before_plan_update() -> None:
    verdict = parse_planner_text(
        "SETTLE_INSTRUCTION=3\n"
        "Decision:\nPROJECT_DONE=false\nREASON=Evaluate the parser.\n"
        "TASK_TITLE=Evaluate parser\nTASK_OBJECTIVE=Compare both parsers.\n"
        "SETTLE_INSTRUCTION=5\n"
        "PLAN_UPDATE=# Research plan\n"
        "TASK_TITLE=Plan prose, not a task\n"
        "SETTLE_INSTRUCTION=8"
    )

    assert verdict.settled_instructions == (5,)
    assert [task.title for task in verdict.new_tasks] == ["Evaluate parser"]


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


def test_an_older_structured_wait_is_ignored() -> None:
    """A wait is no Planner state: a payload an older prompt taught reads as no work."""
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "the external job is still running",
        "waiting": {
            "wait": "job-42 reaches a terminal state",
            "stage_reconciliation_required": True,
            "wake_on": ["subagent_state"],
        },
        "tasks": [],
    })

    assert verdict.handoff == ""
    assert verdict.error == "planner said not done but produced no concrete tasks"


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


def _owns_paths_verdict(raw: str):
    return parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=two independent runs\n"
        "TASK_TITLE=CPC evaluation\n"
        "TASK_OBJECTIVE=Run the assigned arm.\n"
        "TASK_PARALLEL_SAFE=true\n"
        "TASK_OWNS_PATHS=" + raw
    )


def test_key_value_owns_paths_accepts_the_json_array_planners_write() -> None:
    # Project s-a43d1018 wrote this exact line 19 times; it used to become one
    # path containing ``[`` and the task always ran serially.
    shapes = {
        '[".argus/runs/real-transfer01/cpc"]': [".argus/runs/real-transfer01/cpc"],
        ".argus/runs/real-transfer01/cpc": [".argus/runs/real-transfer01/cpc"],
        '[".argus/runs/a", ".argus/runs/b"]': [".argus/runs/a", ".argus/runs/b"],
        ".argus/runs/a|.argus/runs/b": [".argus/runs/a", ".argus/runs/b"],
    }
    for raw, expected in shapes.items():
        verdict = _owns_paths_verdict(raw)
        assert not verdict.error, raw
        assert verdict.new_tasks[0].owns_paths == expected, raw


def test_key_value_owns_paths_rejects_a_malformed_array() -> None:
    for raw in ('[".argus/runs/a"', "[1, 2]", '[["nested"]]', '[{"path": "a"}]'):
        verdict = _owns_paths_verdict(raw)
        assert verdict.error and "TASK_OWNS_PATHS" in verdict.error, raw
        assert verdict.new_tasks == []


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
    assert verdict.new_tasks[0].title == "Retry renamed task"


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


@pytest.mark.parametrize("line, handoff", [
    ("→ engineer", "engineer"),
    ("→ manager", "manager"),
    ("→ 统筹", "manager"),
])
def test_a_planner_without_new_work_names_who_acts_next(line: str, handoff: str) -> None:
    verdict = parse_planner_text(
        "Decision:\nPROJECT_DONE=false\nREASON=The running evaluation decides the next step.\n"
        + line
    )

    assert verdict.error == ""
    assert (verdict.handoff, verdict.operator_question) == (handoff, "")
    assert verdict.new_tasks == []


def test_a_planner_asks_the_operator_on_the_baton_line() -> None:
    verdict = parse_planner_text(
        "Decision:\nPROJECT_DONE=false\nREASON=An operator must approve the external action.\n"
        "→ operator: May I publish the dataset?"
    )

    assert verdict.error == ""
    assert verdict.handoff == "operator"
    assert verdict.operator_question == "May I publish the dataset?"
    assert verdict.operator_options == ()


def test_a_planner_question_carries_the_answers_listed_under_it() -> None:
    verdict = parse_planner_text(
        "Decision:\nPROJECT_DONE=false\nREASON=Two directions fit the goal.\n"
        "→ operator: Which direction next?\n- Scale the baseline\n- Try the new loss"
    )

    assert verdict.error == ""
    assert (verdict.handoff, verdict.operator_question, verdict.operator_options) == (
        "operator", "Which direction next?", ("Scale the baseline", "Try the new loss"),
    )


def test_older_wait_fields_are_ignored_and_tasks_still_run() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=One branch waits for operator input; an independent check can run.\n"
        "WAITING=true\n"
        "WAIT=Operator provides the choice.\n"
        "OPERATOR_ACTION_REQUIRED=true\n"
        "TASK_KEY=independent-check\n"
        "TASK_TITLE=Run independent check\n"
        "TASK_OBJECTIVE=Collect evidence that does not depend on the operator choice."
    )

    assert verdict.error == ""
    assert verdict.handoff == ""
    assert [task.key for task in verdict.new_tasks] == ["independent-check"]
    assert verdict.new_tasks[0].objective == (
        "Collect evidence that does not depend on the operator choice."
    )


def test_a_closing_baton_line_does_not_run_on_into_the_objective() -> None:
    verdict = parse_planner_text(
        "Decision:\nPROJECT_DONE=false\nREASON=Ask and run.\n"
        "TASK_KEY=k1\nTASK_TITLE=Run it\nTASK_OBJECTIVE=Run the check.\n"
        "→ operator: Which dataset?"
    )

    assert verdict.new_tasks[0].objective == "Run the check."
    assert verdict.handoff == "operator"


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
    assert 'Assigning work' in _PLANNER_CORE_CONTRACT
    assert 'Read state, never edit' in _PLANNER_CORE_CONTRACT
    assert 'Engineer implements and tests' in _PLANNER_CORE_CONTRACT
    # 2026-10-22: a task asking for "a different Reviewer" had the Engineer start
    # a reviewer subagent of its own; who does and reviews work is the handoffs' call.
    assert "not who does or reviews it; handoffs decide" in " ".join(
        _PLANNER_CORE_CONTRACT.split()
    )
    assert "ARGUS_ROLE_DECISION=" not in _PLANNER_CORE_CONTRACT
    assert "PROJECT_DONE=false" in _PLANNER_CORE_CONTRACT
    assert "TASK_KEY=k1" in _PLANNER_CORE_CONTRACT
    assert "RETIRE_TASK=<item id> | <one-sentence reason>" in _PLANNER_CORE_CONTRACT
    assert 'never retire running or done work' in _PLANNER_CORE_CONTRACT
    for line in ("`→ engineer`", "`→ manager`", "`→ operator: <one question>`"):
        assert line in _PLANNER_CORE_CONTRACT
    for field in ("WAITING", "WAIT=", "wake_on", "OPERATOR_ACTION_REQUIRED"):
        assert field not in _PLANNER_CORE_CONTRACT
    assert "no `TASK_*` blocks" in _PLANNER_CORE_CONTRACT
    for field in ("`TASK_TITLE`", "`TASK_OBJECTIVE`", "`TASK_ACCEPTANCE_CHECK`"):
        assert field in _PLANNER_CORE_CONTRACT
    for field in (
        "TASK_WORKDIR",
        "TASK_WORK_KIND",
        "TASK_CONTEXT_REFS",
        "TASK_REQUIRE_INDEPENDENT_REVIEW",
        "TASK_STAGE_CLOSING",
    ):
        assert field not in _PLANNER_CORE_CONTRACT
    assert "`TASK_SCOPE`" in _PLANNER_CORE_CONTRACT
    assert '`TASK_SCOPE`\n  defaults to `bounded`' in _PLANNER_CORE_CONTRACT
    assert 'Optional `ADVANCE_TO_STAGE` must be Host-valid; omit to hold' in _PLANNER_CORE_CONTRACT
    assert "enqueue-time validation/normalization" not in _PLANNER_CORE_CONTRACT
    assert "external algorithm" in _PLANNER_CORE_CONTRACT
    assert 'Ground external algorithms beyond Wiki/Skills in primary sources' in _PLANNER_CORE_CONTRACT
    assert 'beyond Wiki/Skills' in _PLANNER_CORE_CONTRACT
    assert 'Consult papers,\n  source, issues, or hardware when consequential' in _PLANNER_CORE_CONTRACT
    assert 'Repeated failures require rereading' in (
        _PLANNER_CORE_CONTRACT
    )
    assert 'official code' in _PLANNER_CORE_CONTRACT


def test_planner_footer_example_is_an_ambitious_experiment_program() -> None:
    footer = _PLANNER_DECISION_FOOTER
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
    assert 'Report what happened and what comes next.' in finite
    assert "what happened" in finite
    assert 'timing, profiling' in finite
    assert 'Continue until stopped by the operator' not in finite
    assert 'Continue until stopped by the operator' in standing


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
    assert verdict.new_tasks[0].deps[0].startswith("No_20_external_20_dependency._20_")
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


def test_plan_next_reads_the_skill_libraries_its_prompt_names(tmp_path, monkeypatch) -> None:
    """s-757b9dac: the read-only Planner was pointed at the shared vertical
    library but not given it, so its lookup there failed."""
    from argus.skills.layered import LayeredSkillStore

    store = LayeredSkillStore(project_dir=tmp_path / "project-skills", global_dir=tmp_path / "shared-skills")
    runner = _Runner()
    monkeypatch.setattr(Planner, "_build_planner_prompt", staticmethod(lambda **kwargs: "prompt"))

    Planner(runner, skill_store=store).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(working_dir=str(tmp_path / "work"), add_dirs=["/tmp/project-state"]),
    )

    add_dirs = runner.calls[0]["options"].add_dirs
    assert add_dirs[0] == "/tmp/project-state"
    assert {str(root) for root in Planner(None, skill_store=store).mission.libraries().library_roots} <= set(add_dirs)
    assert str((tmp_path / "shared-skills").resolve()) in add_dirs


@pytest.mark.parametrize("offered", [(), (7, 9)])
def test_plan_next_names_settle_line_last_only_when_entries_are_offered(
    monkeypatch, offered,
) -> None:
    # The shared footer asks for "only the Host actions below"; without this
    # closing line a live Planner offered tagged entries settled none of them.
    runner = _Runner()
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "planner prompt"),
    )

    Planner(runner).plan_next(
        continuous_objective="keep improving",
        config=PlannerConfig(
            working_dir="/tmp/project", settleable_instructions=offered,
        ),
    )

    prompt = runner.calls[0]["prompt"]
    if offered:
        assert prompt.endswith(
            "(for a subagent report) has already taken into account; omit "
            "the line when none does."
        )
        assert "`SETTLE_INSTRUCTION=N M` (space-separated revisions)" in prompt
    else:
        assert "SETTLE_INSTRUCTION" not in prompt


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
    assert "If work can start now, include concrete tasks" in runner.calls[1]["prompt"]
    assert "TASK_TITLE=Run the next decisive check" in runner.calls[1]["prompt"]
    assert (
        "TASK_OBJECTIVE=execute the concrete check required by current evidence"
        in runner.calls[1]["prompt"]
    )
    assert runner.calls[1]["options"].working_dir == "/tmp/project"


def test_repair_asks_a_planner_without_tasks_who_acts_next(monkeypatch) -> None:
    # The response named what is in flight but no task and no holder.
    fields = (
        "PROJECT_DONE=false\n"
        "REASON=Wait for the existing blind test to finish.\n"
    )
    runner = _SequenceRunner([fields, fields + "→ engineer"])
    monkeypatch.setattr(
        Planner, "_build_planner_prompt", staticmethod(lambda **kwargs: "planner prompt")
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="assess the blind test",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    assert len(runner.calls) == 2
    repair = runner.calls[1]["prompt"]
    assert "one closing line naming who acts next: `→ engineer`" in repair
    assert "WAITING" not in repair
    assert verdict.error == ""
    assert verdict.handoff == "engineer"
    assert verdict.project_done is False
    assert verdict.new_tasks == []


@pytest.mark.parametrize("project_done", ["false", "true"])
def test_repair_preserves_certified_increment_operator_handoff(
    monkeypatch, tmp_path, project_done,
) -> None:
    reason = "Current final certification is accepted."
    footer = (
        "PROJECT_DONE=false\n"
        f"REASON={reason}\n"
        "→ operator: What should the campaign take on next?\n"
    )
    runner = _SequenceRunner([
        f"PROJECT_DONE={project_done}\nREASON={reason}",
        footer,
    ])
    monkeypatch.setattr(
        Planner, "_build_planner_prompt",
        staticmethod(lambda **kwargs: (
            "The final stage is complete and the current increment is certified. "
            "No active backlog or live subagent work remains. Latest operator "
            "instruction: report only; preserve the artifact and create no new work."
        )),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="keep improving the standing research campaign",
        config=PlannerConfig(working_dir=str(tmp_path), open_ended=True),
    )

    assert len(runner.calls) == 2
    repair = runner.calls[1]["prompt"]
    assert "latest explicit operator instructions" in repair
    assert "no-new-work" in repair
    assert "current final certification" in repair
    assert "final stage is complete" in repair
    assert "no active backlog or live background work remains" in repair
    assert "Uncertified work or a generic empty response" in repair
    assert "Do not set `project_done=true`" in repair
    assert "Delegate the next distinct task, or use" not in repair
    assert "`waiting` only for a real external blocker" not in repair
    assert "Re-inspect current project reality" not in repair
    assert "→ operator: What should the campaign take on next?" in repair
    assert "WAITING" not in repair
    assert runner.calls[1]["resume_thread_id"] == "planner-thread"
    assert verdict.error == ""
    assert verdict.project_done is False
    assert verdict.handoff == "operator"
    assert verdict.operator_question == "What should the campaign take on next?"
    assert verdict.reason == reason
    assert verdict.new_tasks == []


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
    assert verdict.new_tasks[0].key == "ri_3c_REDACTED:openai-key_3e_"
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


def test_missing_stage_holds_valid_task_after_scope_normalization(
    monkeypatch,
) -> None:
    runner = _SequenceRunner([
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=review the isolated candidate",
                "TASK_KEY=review-candidate",
                "TASK_TITLE=Review candidate",
                "TASK_OBJECTIVE=Review exactly the frozen candidate rows.",
                "TASK_SCOPE=bounded；one isolated review mission",
            ]
        ),
    ])
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "original planner prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="audit the candidate",
        planning_cycle=10,
        config=PlannerConfig(
            working_dir="/tmp/project",
            require_stage_decision=True,
            current_stage="solve",
        ),
    )

    assert verdict.error == ""
    assert verdict.advance_to_stage == ""
    assert len(runner.calls) == 1
    assert "advance_to_stage missing; holding current stage" in verdict.diagnostics
    assert verdict.new_tasks[0].scope == "bounded"
    assert verdict.new_tasks[0].objective == (
        "Review exactly the frozen candidate rows."
    )


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


@pytest.mark.parametrize("open_ended", [False, True])
def test_plan_next_reports_bounded_failure_after_empty_task_repair_exhaustion(
    monkeypatch, open_ended,
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
        config=PlannerConfig(working_dir="/tmp/project", open_ended=open_ended),
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


def test_planner_zero_exit_fatal_error_preserves_actionable_reason() -> None:
    class Runner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                fatal_error="backend stream ended before a planner decision",
                stderr_lines=["ignored old stderr", "recent stderr detail"],
            )

    verdict = Planner(Runner()).plan_next(
        continuous_objective="keep optimizing Argus",
        config=PlannerConfig(working_dir="/tmp/project", open_ended=True),
    )

    assert verdict.project_done is False
    assert verdict.error == (
        "backend stream ended before a planner decision\n"
        "ignored old stderr\n"
        "recent stderr detail"
    )


def test_parse_planner_objective_keeps_the_brief_that_follows_it() -> None:
    # An implementation brief is a document: claim, components, interfaces,
    # tests, commands. Everything up to the next field belongs to the objective,
    # including blank lines, fenced commands and a field the reader never heard of.
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=Open the experiment stage.\n"
        "TASK_KEY=exp_spec\n"
        "TASK_TITLE=Author METHOD.md and tests/spec\n"
        "TASK_OBJECTIVE=## Claim\n"
        "The method halves the error at equal budget.\n"
        "\n"
        "## Components to implement this task\n"
        "1. `radial_quadrature`: `src/quadrature.py:RadialQuadrature`\n"
        "\n"
        "## Commands\n"
        "```bash\n"
        "CUDA_VISIBLE_DEVICES=1 python -m pytest tests/spec -v\n"
        "```\n"
        "TASK_STAGE_CLOSING=false\n"
        "TASK_ACCEPTANCE_CHECK=pytest tests/spec exits zero\n"
    )

    assert verdict.error == ""
    task = verdict.new_tasks[0]
    assert task.title == "Author METHOD.md and tests/spec"
    assert task.objective.startswith("## Claim\nThe method halves the error")
    assert "## Components to implement this task" in task.objective
    assert "CUDA_VISIBLE_DEVICES=1 python -m pytest tests/spec -v" in task.objective
    assert "TASK_STAGE_CLOSING" not in task.objective
    assert "pytest tests/spec exits zero" not in task.objective
    assert task.acceptance_check == "pytest tests/spec exits zero"


def test_parse_numbered_planner_objective_keeps_its_following_lines() -> None:
    verdict = parse_planner_text(
        "PROJECT_DONE=false\n"
        "REASON=Delegate.\n"
        "TASK_1_TITLE=Certify\n"
        "TASK_1_OBJECTIVE=## Claim\n"
        "Produce the theorem.\n"
        "- one lemma per file\n"
        "TASK_1_ACCEPTANCE_CHECK=Run the verifier.\n"
    )

    assert verdict.error == ""
    assert verdict.new_tasks[0].objective == (
        "## Claim\nProduce the theorem.\n- one lemma per file"
    )
    assert verdict.new_tasks[0].acceptance_check == "Run the verifier."


def test_parse_planner_reads_the_gpus_a_task_holds() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=two arms can train side by side",
            "TASK_KEY=arm-27b",
            "TASK_DEPS=",
            "TASK_TITLE=Train the 27B arm",
            "TASK_OBJECTIVE=Train it.",
            "TASK_PARALLEL_SAFE=true",
            "TASK_OWNS_PATHS=runs/27b",
            "TASK_GPUS=2",
            "TASK_KEY=writeup",
            "TASK_DEPS=",
            "TASK_TITLE=Draft the method section",
            "TASK_OBJECTIVE=Write it.",
            "TASK_GPUS=not-a-number",
        ])
    )
    assert [task.gpu_count for task in verdict.new_tasks] == [2, 0]


def test_structured_planner_payload_reads_gpu_count_as_number_or_numeric_text() -> None:
    def task(key: str, gpus: object) -> dict:
        return {
            "key": key, "deps": [], "title": f"Train {key}",
            "objective": "Train it.", "scope": "bounded", "gpu_count": gpus,
        }

    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "arms train side by side",
        "tasks": [task("a", 2), task("b", " 1 "), task("c", 3.0), {
            "key": "d", "deps": [], "title": "Write", "objective": "Write it.",
            "scope": "bounded",
        }],
    })

    assert verdict.error == ""
    assert [t.gpu_count for t in verdict.new_tasks] == [2, 1, 3, 0]


@pytest.mark.parametrize("garbage", ["two", -1, True, 1.5, [2], {"n": 2}])
def test_structured_planner_payload_drops_only_the_task_with_a_bad_gpu_count(
    garbage: object,
) -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "two tasks",
        "tasks": [
            {"key": "bad", "deps": [], "title": "Train", "objective": "Train it.",
             "scope": "bounded", "gpu_count": garbage},
            {"key": "good", "deps": [], "title": "Write", "objective": "Write it.",
             "scope": "bounded", "gpu_count": "0"},
        ],
    })

    assert verdict.error == ""
    assert [t.key for t in verdict.new_tasks] == ["good"]
    assert any("task 1 skipped: gpu_count" in d for d in verdict.diagnostics)


def test_planner_reads_the_cores_a_task_keeps_busy() -> None:
    verdict = parse_planner_text(
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=arms fit side by side",
            "TASK_KEY=arm-a",
            "TASK_DEPS=",
            "TASK_TITLE=Fit arm a",
            "TASK_OBJECTIVE=Fit it.",
            "TASK_CPUS=8",
            "TASK_KEY=writeup",
            "TASK_DEPS=",
            "TASK_TITLE=Draft the method section",
            "TASK_OBJECTIVE=Write it.",
        ])
    )
    assert [task.cpu_count for task in verdict.new_tasks] == [8, 0]
    payload = parse_planner_payload({
        "project_done": False,
        "reason": "arms fit side by side",
        "tasks": [
            {"key": "a", "deps": [], "title": "Fit", "objective": "Fit it.",
             "scope": "bounded", "cpu_count": "16"},
            {"key": "b", "deps": [], "title": "Write", "objective": "Write it.",
             "scope": "bounded"},
            {"key": "c", "deps": [], "title": "Bad", "objective": "Bad.",
             "scope": "bounded", "cpu_count": "many"},
        ],
    })
    assert [t.cpu_count for t in payload.new_tasks] == [16, 0]
    assert any("task 3 skipped: cpu_count" in d for d in payload.diagnostics)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"settle_instructions": [7, "revision 9", "x", 7.0, 2.5, True]}, (7, 9)),
        ({"settle_instructions": None, "settle_instruction": 7}, (7,)),
    ],
)
def test_structured_payload_carries_settled_instructions(fields, expected) -> None:
    verdict = parse_planner_payload({
        "project_done": False,
        "reason": "Evaluate the parser.",
        "tasks": [{"title": "Evaluate parser", "objective": "Compare both parsers."}],
        **fields,
    })

    assert verdict.error == ""
    assert verdict.settled_instructions == expected


def test_repair_prompt_asks_to_repeat_settlements_only_when_present() -> None:
    from argus.planner.planner import _build_no_task_repair_prompt

    with_settle = _build_no_task_repair_prompt(
        previous_raw_text="PROJECT_DONE=false\nSETTLE_INSTRUCTION=7",
        previous_error="planner returned no concrete tasks",
    )
    without = _build_no_task_repair_prompt(
        previous_raw_text="PROJECT_DONE=false",
        previous_error="planner returned no concrete tasks",
    )

    assert "Repeat each `SETTLE_INSTRUCTION` line" in with_settle
    assert "Repeat each `SETTLE_INSTRUCTION` line" not in without


def test_plan_update_keeps_a_quoted_settle_line_as_plan_text(tmp_path) -> None:
    from types import SimpleNamespace

    from argus.life.research_plan import research_plan_path
    from argus.life.supervisor._planner_rendering import PlannerRenderingMixin

    class _Probe(PlannerRenderingMixin):
        def __init__(self) -> None:
            self.memory = SimpleNamespace(root=tmp_path)
            self.events: list[dict] = []

        def _emit(self, event: dict) -> None:
            self.events.append(event)

    plan = (
        "# Research plan\nShip the faster parser.\n"
        "## Central hypotheses\n- The parser is the bottleneck.\n"
        "## Experiment program\n- Compare both parsers.\n"
        "SETTLE_INSTRUCTION=7\n"
        "## Established results\n- None yet.\n"
        "## Dead ends\n- None yet.\n"
        "## Next milestone\n- Parser comparison recorded."
    )

    assert _Probe()._apply_research_plan_update(
        "Decision:\nPROJECT_DONE=false\nREASON=Plan the comparison.\nPLAN_UPDATE=" + plan
    )
    saved = research_plan_path(tmp_path).read_text(encoding="utf-8")
    assert "## Next milestone" in saved
    assert "SETTLE_INSTRUCTION=7" in saved
