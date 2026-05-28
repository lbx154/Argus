"""Unit tests for the Planner sub-agent: parsing + plan_next dispatch.

The Planner is stateless; we exercise the JSON parser end-to-end and the
plan_next dispatch path that the supervisor relies on. The historical
"critic" surface (per-iteration polish loop with Improvement records)
has been removed; if you came here looking for those tests, they were
deleted along with the dead code.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.planner import (
    Planner,
    PlannerConfig,
    PlannerVerdict,
    TaskSpec,
    parse_planner_text,
)


class _FakeRunner:
    def __init__(self, *agent_messages: str) -> None:
        self._agent_messages = list(agent_messages)
        self.calls: list[tuple[str, object]] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append((prompt, options))
        return RunnerResult(
            exit_code=0,
            agent_messages=list(self._agent_messages),
            stdout_lines=[],
            stderr_lines=[],
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


# ---------------------------------------------------------------------------
# parse_planner_text
# ---------------------------------------------------------------------------


def test_parse_planner_text_project_done_clears_tasks() -> None:
    txt = json.dumps({"project_done": True, "reason": "ok", "new_tasks": []})
    v = parse_planner_text(txt)
    assert v.project_done is True
    assert v.new_tasks == []


def test_parse_planner_text_emits_task_specs() -> None:
    txt = json.dumps({
        "project_done": False,
        "reason": "more work",
        "new_tasks": [{
            "title": "fix the loader",
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "loader crashes on empty input",
            "scope": "bounded",
            "objective": "patch code/loader.py to handle empty input and add a regression test",
        }],
    })
    v = parse_planner_text(txt)
    assert v.project_done is False
    assert len(v.new_tasks) == 1
    spec = v.new_tasks[0]
    assert isinstance(spec, TaskSpec)
    assert spec.title == "fix the loader"
    assert spec.impact_score == 5
    assert spec.scope == "bounded"


def test_parse_planner_text_returns_error_verdict_on_garbage() -> None:
    v = parse_planner_text("not json at all")
    assert v.project_done is False
    assert v.error or v.new_tasks == []


# ---------------------------------------------------------------------------
# Planner.plan_next dispatch
# ---------------------------------------------------------------------------


def test_plan_next_returns_done_when_runner_says_so() -> None:
    runner = _FakeRunner(json.dumps({
        "project_done": True,
        "reason": "everything is done",
        "new_tasks": [],
    }))
    verdict = Planner(runner).plan_next(
        continuous_objective="keep going",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
    )
    assert verdict.project_done is True
    assert verdict.new_tasks == []
    assert len(runner.calls) == 1


def test_plan_next_passes_planner_config_to_runner() -> None:
    runner = _FakeRunner(json.dumps({"project_done": True, "reason": "x", "new_tasks": []}))
    cfg = PlannerConfig(
        model="gpt-test",
        reasoning_effort="high",
        working_dir="/tmp/planner",
        skip_git_repo_check=True,
        full_auto=False,
        dangerous_yolo=True,
    )
    Planner(runner).plan_next(
        continuous_objective="goal",
        journal_tail="recent work",
        budget_remaining_usd=1.0,
        planning_cycle=2,
        runtime_change_summary="Runtime source changed since daemon start.",
        config=cfg,
    )
    sent_prompt, opts = runner.calls[0]
    assert opts.model == "gpt-test"
    assert opts.reasoning_effort == "high"
    assert opts.working_dir == "/tmp/planner"
    assert opts.dangerous_yolo is True
    # Structural prompt assertions only — verify the planner prompt
    # carries the runtime context + the current stage checklist headline.
    assert "Runtime source changed since daemon start." in sent_prompt
    assert "## Stage checklist" in sent_prompt


def test_plan_next_returns_error_verdict_on_runner_exception() -> None:
    class _BrokenRunner:
        def run_exec(self, **_):
            raise RuntimeError("backend exit 127")

    verdict = Planner(_BrokenRunner()).plan_next(
        continuous_objective="goal",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
    )
    assert verdict.project_done is False
    assert verdict.error
