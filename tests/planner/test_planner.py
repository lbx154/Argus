"""Unit tests for the Planner sub-agent: parsing + plan_next dispatch.

The Planner is stateless; we exercise the JSON parser end-to-end and the
plan_next dispatch path that the supervisor relies on. The historical
"critic" surface (per-iteration polish loop with Improvement records)
has been removed; if you came here looking for those tests, they were
deleted along with the dead code.
"""
from __future__ import annotations

import json

from argus_skill.core.models import RunnerResult
from argus_skill.planner import (
    Planner,
    PlannerConfig,
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


def test_parse_planner_text_uses_latest_json_verdict() -> None:
    placeholder = json.dumps({
        "project_done": False,
        "reason": "inspecting before routing",
        "restart_daemon": False,
        "waiting": False,
        "new_tasks": [],
    })
    final = json.dumps({
        "project_done": False,
        "reason": "route the no-go pivot",
        "restart_daemon": False,
        "waiting": False,
        "new_tasks": [{
            "title": "Rollback and pivot the failed positive method plan",
            "impact_score": 5,
            "impact_area": "requirement_gap",
            "evidence": "research/CLAIM_REPAIR_NO_GO.md blocks the positive claim",
            "scope": "bounded",
            "objective": (
                "Roll back to plan, inspect the no-go evidence, and produce "
                "the next pivot plan."
            ),
        }],
    })

    v = parse_planner_text(placeholder + "\n" + final)

    assert not v.error
    assert len(v.new_tasks) == 1
    assert v.reason == "route the no-go pivot"
    assert v.new_tasks[0].title == "Rollback and pivot the failed positive method plan"


def test_parse_planner_text_returns_error_verdict_on_garbage() -> None:
    v = parse_planner_text("not json at all")
    assert v.project_done is False
    assert v.error or v.new_tasks == []


def test_parse_planner_text_waiting_is_not_error() -> None:
    # First-class await-external: planner intentionally idles with no tasks
    # and no done. This must NOT be reported as an error (which would spin /
    # make-work); it is a clean waiting outcome.
    txt = json.dumps({
        "project_done": False,
        "reason": "training run still in progress; nothing higher-impact to do",
        "new_tasks": [],
        "waiting": True,
        "waiting_reason": "CV-GRPO run 2b510 at step 40/200, not terminal",
    })
    v = parse_planner_text(txt)
    assert v.waiting is True
    assert v.project_done is False
    assert v.new_tasks == []
    assert not v.error
    assert "CV-GRPO" in v.waiting_reason


def test_parse_planner_text_no_tasks_without_waiting_is_error() -> None:
    # No tasks, not done, and waiting NOT set → still treated as a degenerate
    # planner output (error), preserving the original safety net.
    txt = json.dumps({
        "project_done": False,
        "reason": "hmm",
        "new_tasks": [],
    })
    v = parse_planner_text(txt)
    assert v.waiting is False
    assert v.error


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


# ---------------------------------------------------------------------------
# Parallel paper-drafting track (run/analysis only) — _build_planner_prompt
# ---------------------------------------------------------------------------


def _prompt_for_stage(monkeypatch, tmp_path, stage: str) -> str:
    """Build the planner prompt with current_stage pinned to ``stage``."""
    from argus_skill.skills import harness_overlay, stage_checklists

    monkeypatch.setattr(stage_checklists, "current_stage", lambda *_a, **_k: stage)
    monkeypatch.setattr(
        stage_checklists,
        "format_stage_checklist",
        lambda s, **_k: f"<<CHECKLIST:{s}>>",
    )
    monkeypatch.setattr(
        harness_overlay, "resolve_project_root", lambda *_a, **_k: tmp_path
    )
    return Planner._build_planner_prompt(
        continuous_objective="write the EMNLP paper",
        journal_tail="",
        budget_remaining_usd=50.0,
        planning_cycle=0,
        runtime_change_summary="",
    )


def test_parallel_drafting_block_present_at_run(monkeypatch, tmp_path) -> None:
    prompt = _prompt_for_stage(monkeypatch, tmp_path, "run")
    assert "## Parallel paper-drafting track" in prompt
    # Draft-stage checklist is surfaced for scoping.
    assert "<<CHECKLIST:draft>>" in prompt
    # Integrity + non-advancement guardrails are present.
    assert "PIPELINE_STATE.json" in prompt
    assert "RESULT_PLACEHOLDERS.md" in prompt
    assert "TBD" in prompt
    # Reviewer framing names the current stage as background-only.
    assert "BACKGROUND context only" in prompt


def test_parallel_drafting_block_present_at_analysis(monkeypatch, tmp_path) -> None:
    prompt = _prompt_for_stage(monkeypatch, tmp_path, "analysis")
    assert "## Parallel paper-drafting track" in prompt
    assert "<<CHECKLIST:draft>>" in prompt
    # analysis-stage gets the evidence_chain structural caveat.
    assert "evidence_chain" in prompt


def test_parallel_drafting_block_absent_outside_run_analysis(
    monkeypatch, tmp_path
) -> None:
    for stage in ("research", "plan", "benchmark", "draft", "review", "submission"):
        prompt = _prompt_for_stage(monkeypatch, tmp_path, stage)
        assert "## Parallel paper-drafting track (run/analysis only)" not in prompt, stage
        assert "RESULT_PLACEHOLDERS.md" not in prompt, stage


def test_rule7_exception_documented_in_preamble() -> None:
    from argus_skill.planner.planner import _PLANNER_SYSTEM_PREAMBLE

    assert "Parallel paper-drafting track" in _PLANNER_SYSTEM_PREAMBLE
    assert "EXCEPTION" in _PLANNER_SYSTEM_PREAMBLE
