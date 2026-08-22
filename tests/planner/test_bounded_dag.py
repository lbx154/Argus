from __future__ import annotations

import json

from argus_skill.core.models import RunnerResult
from argus_skill.planner.bounded_dag import plan_bounded_dag


class _Runner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        lines = [f"PLAN_REASON={self.payload['reason']}"]
        for task in self.payload["tasks"]:
            lines.extend([
                f"TASK_KEY={task['key']}",
                f"TASK_DEPS={','.join(task['deps'])}",
                f"TASK_TITLE={task['title']}",
                f"TASK_OBJECTIVE={task['objective']}",
                f"TASK_HYPOTHESIS={task.get('hypothesis', 'This task tests its stated mechanism.')}",
                f"TASK_GOAL_CONTRIBUTION={task.get('goal_contribution', 'This task advances the requested deliverable.')}",
                f"TASK_EXPECTED_REGRESSIONS={task.get('expected_regressions', 'None expected beyond local work in progress.')}",
                f"TASK_DECISION_RULE={task.get('decision_rule', 'Replan if the mechanism cannot satisfy the user goal.')}",
                f"TASK_SCOPE={task.get('scope', 'bounded')}",
                f"TASK_STAGE_CLOSING={task.get('stage_closing', 'false')}",
                "TASK_REQUIRE_INDEPENDENT_REVIEW="
                f"{task.get('require_independent_review', 'false')}",
                "TASK_SKIP_STAGE_TRANSITION="
                f"{task.get('skip_stage_transition', 'false')}",
                "TASK_OPERATOR_APPROVAL_REQUIRED="
                f"{task.get('operator_approval_required', 'false')}",
                "TASK_ALLOW_SKILL_CHANGES="
                f"{task.get('allow_skill_changes', 'false')}",
            ])
            for key in (
                "acceptance_check",
                "non_goals",
                "context_refs",
                "vertical",
            ):
                if key in task:
                    field = f"TASK_{key.upper()}"
                    lines.append(f"{field}={task[key]}")
        return RunnerResult(
            exit_code=0,
            agent_messages=["\n".join(lines)],
            input_tokens=100,
            output_tokens=20,
        )


class _RawRunner:
    def __init__(self, text: str) -> None:
        self.text = text

    def run_exec(self, **_kwargs):
        return RunnerResult(exit_code=0, agent_messages=[self.text])


def test_bounded_planner_parses_real_fanout_fanin_dag(tmp_path) -> None:
    runner = _Runner(
        {
            "reason": "separate implementation from independent verification",
            "tasks": [
                {
                    "key": "a",
                    "deps": [],
                    "title": "Implement parser",
                    "objective": "write src/parser.py; run pytest tests/test_parser.py",
                },
                {
                    "key": "b",
                    "deps": [],
                    "title": "Build fixtures",
                    "objective": "write tests/fixtures.json; validate JSON parsing",
                    "vertical": "argus_maintenance",
                },
                {
                    "key": "c",
                    "deps": ["a", "b"],
                    "title": "Integrate CLI",
                    "objective": "read src/parser.py and tests/fixtures.json; write src/cli.py; run pytest -q",
                    "acceptance_check": "pytest -q exits zero",
                    "non_goals": "do not publish|do not edit pipeline state",
                    "context_refs": (
                        "artifact::research/chem_playground/x/QUESTION.md::question|"
                        "artifact::research/chem_playground/x/RESULT.md::result"
                    ),
                    "scope": "bounded",
                    "stage_closing": "false",
                    "require_independent_review": "true",
                    "skip_stage_transition": "true",
                },
            ],
        }
    )

    plan = plan_bounded_dag(runner, "build the tool", workdir=tmp_path)

    assert not plan.error
    assert [task.key for task in plan.tasks] == ["a", "b", "c"]
    assert plan.tasks[2].deps == ("a", "b")
    assert plan.tasks[1].vertical == "argus_maintenance"
    assert plan.tasks[2].acceptance_check == "pytest -q exits zero"
    assert plan.tasks[2].non_goals == ("do not publish", "do not edit pipeline state")
    assert not hasattr(plan.tasks[2], "context_refs")
    prompt = runner.calls[0]["prompt"]
    assert "primary-source semantics are materially missing" in prompt
    assert "Existing grounding never forbids fresh upstream research" in prompt
    assert "When related attempts repeatedly fail" in prompt
    assert "`deps` (same-batch keys only)" in prompt
    assert plan.tasks[2].require_independent_review is True
    call = runner.calls[0]
    assert call["run_label"] == "planner.bounded_dag"
    assert call["options"].working_dir == str(tmp_path.resolve())
    assert not hasattr(call["options"], "output_schema_path")
    assert "ARGUS_ROLE_DECISION=" in call["prompt"]
    assert "Any later response is plain language and is not parsed." in call["prompt"]
    assert "TASK_CONTEXT_REFS" not in call["prompt"]
    assert "TASK_STAGE_CLOSING" not in call["prompt"]
    assert "`require_independent_review:true`" in call["prompt"]
    assert "The Host owns execution and enforces review policy" in call["prompt"]
    assert "Never create a review-only or validation-only task" in call["prompt"]


def test_bounded_planner_accepts_observed_control_value_explanations(
    tmp_path,
) -> None:
    runner = _RawRunner(
        "\n".join([
            "PLAN_REASON=one grounded task",
            "TASK_KEY=grounded",
            (
                "TASK_DEPS=No external dependency. Preserve the dense baseline; "
                "do not repeat rejected work."
            ),
            "TASK_TITLE=Implement grounded method",
            "TASK_OBJECTIVE=Implement and test the source-backed method.",
            "TASK_HYPOTHESIS=The grounded method fits the local architecture.",
            "TASK_GOAL_CONTRIBUTION=Advance the operator objective.",
            "TASK_EXPECTED_REGRESSIONS=The candidate may be slower.",
            "TASK_DECISION_RULE=Reject on parity or performance regression.",
            "TASK_SCOPE=bounded — one coherent mission",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            "TASK_SKIP_STAGE_TRANSITION=false",
            "TASK_OPERATOR_APPROVAL_REQUIRED=false",
            "TASK_ALLOW_SKILL_CHANGES=false",
        ])
    )

    plan = plan_bounded_dag(runner, "implement the method", workdir=tmp_path)

    assert plan.error == ""
    assert plan.tasks[0].deps == ()
    assert not hasattr(plan.tasks[0], "scope")
    assert plan.tasks[0].require_independent_review is True


def test_bounded_planner_rejects_cycle(tmp_path) -> None:
    runner = _Runner(
        {
            "reason": "bad graph",
            "tasks": [
                {"key": "a", "deps": ["b"], "title": "A", "objective": "do A"},
                {"key": "b", "deps": ["a"], "title": "B", "objective": "do B"},
            ],
        }
    )

    plan = plan_bounded_dag(runner, "x", workdir=tmp_path)

    assert "cycle" in plan.error


def test_bounded_planner_rejects_unicode_equivalent_duplicate_keys(tmp_path) -> None:
    runner = _Runner(
        {
            "reason": "bad graph",
            "tasks": [
                {"key": "café", "deps": [], "title": "A", "objective": "do A"},
                {"key": "cafe\u0301", "deps": [], "title": "B", "objective": "do B"},
            ],
        }
    )

    plan = plan_bounded_dag(runner, "x", workdir=tmp_path)

    assert "duplicate" in plan.error


def test_bounded_planner_rejects_casefold_equivalent_duplicate_keys(
    tmp_path,
) -> None:
    runner = _Runner(
        {
            "reason": "bad graph",
            "tasks": [
                {"key": "Build", "deps": [], "title": "A", "objective": "do A"},
                {"key": "build", "deps": [], "title": "B", "objective": "do B"},
            ],
        }
    )

    plan = plan_bounded_dag(runner, "x", workdir=tmp_path)

    assert "duplicate" in plan.error


def test_bounded_planner_accepts_minimal_task_without_control_fields(tmp_path) -> None:
    plan = plan_bounded_dag(
        _RawRunner(
            "PLAN_REASON=truncated footer\n"
            "TASK_KEY=a\n"
            "TASK_DEPS=\n"
            "TASK_TITLE=A\n"
            "TASK_OBJECTIVE=do A"
        ),
        "x",
        workdir=tmp_path,
    )

    assert plan.error == ""
    assert plan.tasks[0].title == "A"
    assert plan.tasks[0].require_independent_review is False


def test_bounded_planner_carries_structured_independent_review_policy(
    tmp_path,
) -> None:
    decision = {
        "role": "planner",
        "payload": {
            "reason": "the operator requested independent review",
            "tasks": [{
                "key": "implement",
                "deps": [],
                "title": "Implement",
                "objective": "implement and test the feature",
                "non_goals": "do not publish",
                "require_independent_review": True,
            }],
        },
    }

    plan = plan_bounded_dag(
        _RawRunner(f"ARGUS_ROLE_DECISION={json.dumps(decision)}"),
        "implement and independently review the feature",
        workdir=tmp_path,
    )

    assert plan.error == ""
    assert plan.tasks[0].non_goals == ("do not publish",)
    assert plan.tasks[0].require_independent_review is True


def test_bounded_planner_resolves_casefolded_dep_to_canonical_key(tmp_path) -> None:
    runner = _Runner(
        {
            "reason": "normalized dep",
            "tasks": [
                {"key": "Parent", "deps": [], "title": "A", "objective": "do A"},
                {"key": "child", "deps": ["parent"], "title": "B", "objective": "do B"},
            ],
        }
    )

    plan = plan_bounded_dag(runner, "x", workdir=tmp_path)

    assert plan.error == ""
    assert plan.tasks[1].deps == ("Parent",)


def test_bounded_planner_does_not_cap_node_count(tmp_path) -> None:
    runner = _Runner(
        {
            "reason": "too many overlapping stages",
            "tasks": [
                {"key": str(index), "deps": [], "title": f"Task {index}", "objective": "work"}
                for index in range(12)
            ],
        }
    )

    plan = plan_bounded_dag(runner, "one cohesive change", workdir=tmp_path)

    assert not plan.error
    assert len(plan.tasks) == 12
