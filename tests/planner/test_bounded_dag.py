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
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(self.payload)],
            input_tokens=100,
            output_tokens=20,
        )


def test_bounded_planner_parses_real_fanout_fanin_dag(tmp_path) -> None:
    runner = _Runner({
        "reason": "separate implementation from independent verification",
        "tasks": [
            {"key": "a", "deps": [], "title": "Implement parser", "objective": "write src/parser.py; run pytest tests/test_parser.py"},
            {"key": "b", "deps": [], "title": "Build fixtures", "objective": "write tests/fixtures.json; validate JSON parsing"},
            {"key": "c", "deps": ["a", "b"], "title": "Integrate CLI", "objective": "read src/parser.py and tests/fixtures.json; write src/cli.py; run pytest -q"},
        ],
    })

    plan = plan_bounded_dag(runner, "build the tool", workdir=tmp_path)

    assert not plan.error
    assert [task.key for task in plan.tasks] == ["a", "b", "c"]
    assert plan.tasks[2].deps == ("a", "b")
    call = runner.calls[0]
    assert call["run_label"] == "planner.bounded_dag"
    assert call["options"].working_dir == str(tmp_path.resolve())
    assert "one fresh Engineer session" in call["prompt"]
    assert "Do not initialize Git" in call["prompt"]
    assert "Never create standalone inspect/audit/planning" in call["prompt"]
    assert "The Engineer decides" in call["prompt"]
    assert "framework-required gates may still force review" in call["prompt"]
    assert "Every node pays for a full Engineer + Reviewer cycle" not in call["prompt"]


def test_bounded_planner_rejects_cycle(tmp_path) -> None:
    runner = _Runner({
        "reason": "bad graph",
        "tasks": [
            {"key": "a", "deps": ["b"], "title": "A", "objective": "do A"},
            {"key": "b", "deps": ["a"], "title": "B", "objective": "do B"},
        ],
    })

    plan = plan_bounded_dag(runner, "x", workdir=tmp_path)

    assert "cycle" in plan.error


def test_bounded_planner_does_not_cap_node_count(tmp_path) -> None:
    runner = _Runner({
        "reason": "too many overlapping stages",
        "tasks": [
            {"key": str(index), "deps": [], "title": f"Task {index}", "objective": "work"}
            for index in range(12)
        ],
    })

    plan = plan_bounded_dag(runner, "one cohesive change", workdir=tmp_path)

    assert not plan.error
    assert len(plan.tasks) == 12
