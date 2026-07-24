from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from argus_skill.planner.planner import PLANNER_SCHEMA_PATH, parse_planner_text
from argus_skill.roles.prompts.planner import _PLANNER_CORE_CONTRACT


def test_parse_minimal_task_batch() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "Run the decisive experiment.",
        "new_tasks": [{
            "key": "experiment",
            "deps": [],
            "title": "Run decisive experiment",
            "objective": "Execute the public benchmark and preserve native outputs.",
        }],
    }))

    assert verdict.error == ""
    assert verdict.project_done is False
    assert len(verdict.new_tasks) == 1
    task = verdict.new_tasks[0]
    assert task.key == "experiment"
    assert task.deps == []
    assert task.title == "Run decisive experiment"


def test_parse_dependency_batch() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "Prepare, then run.",
        "new_tasks": [
            {
                "key": "prepare",
                "deps": [],
                "title": "Prepare environment",
                "objective": "Install and smoke-test the selected scientific tool.",
            },
            {
                "key": "run",
                "deps": ["prepare"],
                "title": "Run experiment",
                "objective": "Run the experiment after the environment is ready.",
            },
        ],
    }))

    assert verdict.error == ""
    assert verdict.new_tasks[1].deps == ["prepare"]


def test_project_done_requires_empty_task_batch() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": True,
        "reason": "Complete.",
        "new_tasks": [{
            "key": "extra",
            "deps": [],
            "title": "Extra",
            "objective": "This contradicts project_done.",
        }],
    }))

    assert verdict.project_done is False
    assert verdict.error


def test_not_done_requires_a_task() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "More work remains.",
        "new_tasks": [],
    }))

    assert verdict.error == "planner said not done but produced no concrete tasks"


def test_garbage_is_retryable_error() -> None:
    verdict = parse_planner_text("not json")
    assert verdict.project_done is False
    assert verdict.error


def test_planner_schema_is_minimal_and_strict() -> None:
    schema = json.loads(Path(PLANNER_SCHEMA_PATH).read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"project_done", "reason", "new_tasks"}
    task = schema["properties"]["new_tasks"]["items"]
    assert task["additionalProperties"] is False
    assert set(task["required"]) == {"key", "deps", "title", "objective"}

    jsonschema.validate({
        "project_done": False,
        "reason": "Continue.",
        "new_tasks": [{
            "key": "",
            "deps": [],
            "title": "Inspect evidence",
            "objective": "Inspect the current evidence and choose the next experiment.",
        }],
    }, schema)


def test_planner_prompt_does_not_request_removed_control_fields() -> None:
    for removed in (
        "waiting_contract",
        "checklist_ops",
        "impact_score",
        "impact_area",
        "acceptance_check",
        "non_goals",
        "context_refs",
        "stage_closing",
    ):
        assert removed not in _PLANNER_CORE_CONTRACT


def test_planner_prompt_keeps_value_and_stage_authority() -> None:
    assert "next high-value move" in _PLANNER_CORE_CONTRACT
    assert "Manager alone changes" in _PLANNER_CORE_CONTRACT
