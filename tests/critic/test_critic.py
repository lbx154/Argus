"""Unit tests for the Critic sub-agent: parsing + objective rendering.

These tests use canned text inputs only; the Critic class itself takes
a ``RunnerBackend`` and is exercised indirectly via the iteration-loop
integration test.
"""
from __future__ import annotations

import json
from typing import Any, cast

from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.critic import (
    Critic,
    CriticConfig,
    CriticVerdict,
    Improvement,
    PlannerVerdict,
    parse_critic_text,
    parse_planner_text,
    render_iteration_objective,
)


def _impact_improvement(
    title: str = "add property test",
    *,
    rationale: str = "covers neg amounts",
    acceptance: str = "pytest passes",
    impact_score: int = 4,
    impact_area: str = "correctness",
    evidence: str = "missing edge-case coverage",
) -> dict[str, object]:
    return {
        "title": title,
        "rationale": rationale,
        "acceptance": acceptance,
        "impact_score": impact_score,
        "impact_area": impact_area,
        "evidence": evidence,
    }


def _impact_task(
    title: str = "fix tests",
    objective: str = "run pytest and fix failures",
    *,
    impact_score: int = 4,
    impact_area: str = "reliability",
    evidence: str = "test suite currently lacks this verification",
    scope: str | None = None,
) -> dict[str, object]:
    task = {
        "title": title,
        "objective": objective,
        "impact_score": impact_score,
        "impact_area": impact_area,
        "evidence": evidence,
    }
    if scope is not None:
        task["scope"] = scope
    return task

# ---------------------------------------------------------------------------
# parse_critic_text
# ---------------------------------------------------------------------------


def test_parse_stop_true_clears_improvements():
    txt = '{"stop": true, "reason": "all done", "improvements": [{"title": "noop", "acceptance": "x"}]}'
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is True
    assert v.improvements == []  # discarded when stop=True


def test_parse_stop_false_with_no_improvements_flips_to_stop():
    txt = '{"stop": false, "reason": "", "improvements": []}'
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is True
    assert "no concrete" in v.reason.lower()


def test_parse_stop_false_string_with_no_improvements_flips_to_stop():
    txt = '{"stop": "false", "reason": "", "improvements": []}'
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is True
    assert "no concrete" in v.reason.lower()


def test_parse_continue_with_valid_improvement():
    txt = json.dumps(
        {
            "stop": False,
            "reason": "missing edge cases",
            "improvements": [_impact_improvement()],
        }
    )
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is False
    assert len(v.improvements) == 1
    imp = v.improvements[0]
    assert imp.title == "add property test"
    assert imp.acceptance == "pytest passes"
    assert imp.impact_score == 4
    assert imp.evidence == "missing edge-case coverage"


def test_parse_drops_improvement_missing_acceptance():
    txt = json.dumps({
        "stop": False,
        "reason": "x",
        "improvements": [{
            "title": "polish",
            "rationale": "y",
            "impact_score": 4,
            "impact_area": "correctness",
            "evidence": "missing acceptance should reject this",
        }],
    })
    v = parse_critic_text(txt)
    assert v is not None
    # No valid improvements → flipped to stop
    assert v.stop is True


def test_parse_drops_low_impact_improvement():
    txt = json.dumps({
        "stop": False,
        "reason": "tiny cleanup",
        "improvements": [
            _impact_improvement(
                "rename local variable",
                impact_score=2,
                evidence="would be cleaner",
            )
        ],
    })
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is True
    assert "impact gate" in v.reason


def test_parse_caps_improvements_at_three():
    txt = json.dumps({
        "stop": False,
        "reason": "r",
        "improvements": [
            _impact_improvement(f"t{i}", acceptance=f"a{i}") for i in range(5)
        ],
    })
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is False
    assert len(v.improvements) == 3


def test_parse_handles_markdown_fences_and_prose():
    txt = (
        "Here's my verdict:\n```json\n"
        '{"stop": true, "reason": "looks good", "improvements": []}'
        "\n```\n"
    )
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is True


def test_parse_critic_text_ignores_brace_heavy_prose() -> None:
    txt = (
        "note: {this is not the verdict}\n"
        "```text\nbrace-y prose {still not verdict}\n```\n"
        + json.dumps({
            "stop": False,
            "reason": "needs one more pass",
            "improvements": [
                _impact_improvement(
                    "add edge-case test",
                    rationale="brace noise hid the JSON",
                    acceptance="pytest -q tests/critic/test_critic.py",
                    evidence="parser needs brace-heavy regression coverage",
                )
            ],
        })
        + "\n"
        "postscript: {ignore this too}\n"
    )
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is False
    assert v.improvements[0].title == "add edge-case test"


def test_parse_returns_none_for_empty():
    assert parse_critic_text("") is None
    assert parse_critic_text("no json here") is None


def test_parse_returns_none_for_garbage_json():
    assert parse_critic_text("{not valid json}") is None


# ---------------------------------------------------------------------------
# render_iteration_objective
# ---------------------------------------------------------------------------


def test_render_no_improvements_returns_original():
    out = render_iteration_objective(
        original_objective="ship it", cycles_done=0, improvements=[]
    )
    assert out == "ship it"


def test_render_includes_polish_pass_framing():
    imps = [
        Improvement(
            title="add tests",
            rationale="coverage low",
            acceptance="pytest",
            impact_score=4,
            impact_area="correctness",
            evidence="missing branch coverage",
        ),
        Improvement(
            title="handle err",
            rationale="",
            acceptance="raises ValueError",
            impact_score=5,
            impact_area="reliability",
            evidence="invalid input can crash",
        ),
    ]
    out = render_iteration_objective(
        original_objective="build calculator",
        cycles_done=1,
        improvements=imps,
    )
    assert "cycle #2" in out
    assert "DO NOT rewrite from scratch" in out
    assert "build calculator" in out
    assert "1. add tests" in out
    assert "impact: 4/5" in out
    assert "evidence: missing branch coverage" in out
    assert "2. handle err" in out
    assert "acceptance: pytest" in out


# ---------------------------------------------------------------------------
# Critic.evaluate via fake runner
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Minimal RunnerBackend stand-in: returns a canned message."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.calls: list[tuple[str, RunnerOptions]] = []

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append((prompt, options))
        return RunnerResult(
            exit_code=0,
            agent_messages=[self.message],
        )


def test_evaluate_parses_runner_output():
    runner = _FakeRunner(
        '{"stop": true, "reason": "operator objective fully met", "improvements": []}'
    )
    critic = Critic(runner)
    verdict = critic.evaluate(
        original_objective="add base64 helper",
        latest_completion_summary="implemented in utils, tested",
        cycles_done=0,
        cycles_max=3,
        budget_remaining_usd=2.0,
    )
    assert verdict.stop is True
    assert "fully met" in verdict.reason
    assert len(runner.calls) == 1
    sent_prompt, _ = runner.calls[0]
    assert "add base64 helper" in sent_prompt
    assert "0/3" in sent_prompt or "cycle 0" in sent_prompt
    assert "impact_score" in sent_prompt
    assert "Argus critic role skill" in sent_prompt
    assert "Argus Critic Role" in sent_prompt
    assert "post-review quality filter" in sent_prompt
    assert "planner should find the next valuable mission" in sent_prompt


def test_evaluate_safe_stop_on_unparseable_output():
    runner = _FakeRunner("totally not JSON garble garble")
    verdict = Critic(runner).evaluate(
        original_objective="x",
        latest_completion_summary="y",
        cycles_done=0,
        cycles_max=3,
        budget_remaining_usd=1.0,
    )
    assert verdict.stop is True
    assert "unparse" in verdict.reason.lower()


def test_evaluate_passes_config_to_runner():
    runner = _FakeRunner('{"stop": true, "reason": "ok", "improvements": []}')
    cfg = CriticConfig(
        model="o4-mini",
        reasoning_effort="low",
        working_dir="/tmp/evaluate",
        skip_git_repo_check=False,
        full_auto=True,
        dangerous_yolo=False,
    )
    Critic(runner).evaluate(
        original_objective="x",
        latest_completion_summary="y",
        cycles_done=0,
        cycles_max=3,
        budget_remaining_usd=1.0,
        config=cfg,
    )
    _, opts = runner.calls[0]
    assert opts.model == "o4-mini"
    assert opts.reasoning_effort == "low"
    assert opts.working_dir == "/tmp/evaluate"
    assert opts.skip_git_repo_check is False
    assert opts.full_auto is True
    assert opts.dangerous_yolo is False


def test_plan_next_passes_config_to_runner():
    runner = _FakeRunner('{"project_done": true, "reason": "ok", "new_tasks": []}')
    cfg = CriticConfig(
        model="o4-mini",
        reasoning_effort="low",
        working_dir="/tmp/planner",
        skip_git_repo_check=True,
        full_auto=False,
        dangerous_yolo=True,
    )
    verdict = Critic(runner).plan_next(
        continuous_objective="keep going",
        journal_tail="recent history",
        budget_remaining_usd=1.0,
        planning_cycle=2,
        runtime_change_summary="Runtime source changed since daemon start.",
        config=cfg,
    )
    _, opts = runner.calls[0]
    assert opts.model == "o4-mini"
    assert opts.reasoning_effort == "low"
    assert opts.output_schema_path is not None
    assert opts.output_schema_path.endswith("planner_schema.json")
    assert opts.working_dir == "/tmp/planner"
    assert opts.skip_git_repo_check is True
    assert opts.full_auto is False
    assert opts.dangerous_yolo is True
    assert verdict.project_done is True
    sent_prompt, _ = runner.calls[0]
    assert "Runtime source changed since daemon start." in sent_prompt
    assert "continuous high-value discovery" in sent_prompt
    assert "Argus planner role skill" in sent_prompt
    assert "Argus Planner Role" in sent_prompt
    assert "Validator toolbelt (planner)" in sent_prompt
    assert "validate-full-scale-evidence --project-root ." in sent_prompt
    assert "manager/director" in sent_prompt
    assert "iteration is cheap" not in sent_prompt
    assert '"scope": "<bounded|final_submission>"' in sent_prompt
    assert "validate-full-emnlp --project-root ." in sent_prompt
    assert "paper_contribution" in sent_prompt
    assert "negative-result pivot" in sent_prompt
    assert "long-horizon paper optimization" in sent_prompt
    assert "prefer\n   1 broad task over 3 microtasks" in sent_prompt


def test_plan_next_returns_error_verdict_on_runner_exception():
    class _BoomRunner(_FakeRunner):
        def run_exec(
            self,
            *,
            prompt: str,
            options: RunnerOptions,
            run_label: str,
            resume_thread_id: str | None = None,
        ) -> RunnerResult:
            raise RuntimeError("planner backend exploded")

    verdict = Critic(_BoomRunner("unused")).plan_next(
        continuous_objective="keep going",
        journal_tail="recent history",
        budget_remaining_usd=1.0,
        planning_cycle=0,
        runtime_change_summary="",
    )

    assert verdict.project_done is False
    assert verdict.error
    assert "planner backend exploded" in verdict.error
    assert "RuntimeError" in verdict.raw_text


def test_evaluate_passes_run_label_to_runner() -> None:
    """Regression: CodexRunnerBackend.run_exec REQUIRES run_label kwarg.

    Before this fix the critic crashed with
    ``TypeError: run_exec() missing 1 required keyword-only argument:
    'run_label'`` after the very first iteration cycle on a real codex
    backend (memory backend tolerated the omission).
    """
    runner = _FakeRunner('{"stop": true, "reason": "ok", "improvements": []}')

    captured: dict = {}
    real_run_exec = runner.run_exec

    def strict_run_exec(
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        # Mirror the real backend signature: run_label is REQUIRED kw-only.
        captured["run_label"] = run_label
        return real_run_exec(
            prompt=prompt,
            resume_thread_id=resume_thread_id,
            options=options,
            run_label=run_label,
        )

    cast(Any, runner).run_exec = strict_run_exec
    Critic(runner).evaluate(
        original_objective="x",
        latest_completion_summary="y",
        cycles_done=2,
        cycles_max=3,
        budget_remaining_usd=1.0,
    )
    assert captured["run_label"]
    # Label encodes the cycle number for log/journal correlation.
    assert "critic" in captured["run_label"]
    assert "3" in captured["run_label"]  # cycles_done + 1


def test_critic_verdict_dataclass_is_frozen():
    v = CriticVerdict(stop=True, reason="x")
    try:
        v.stop = False
    except Exception:
        return
    raise AssertionError("CriticVerdict should be frozen")


# ---------------------------------------------------------------------------
# parse_planner_text
# ---------------------------------------------------------------------------

def test_parse_planner_project_done():
    text = '{"project_done": true, "reason": "everything is clean", "new_tasks": []}'
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is True
    assert v.reason == "everything is clean"
    assert v.new_tasks == []


def test_parse_planner_project_done_string_false():
    text = json.dumps({
        "project_done": "false",
        "reason": "needs work",
        "new_tasks": [_impact_task()],
    })
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert len(v.new_tasks) == 1


def test_parse_planner_new_tasks():
    text = json.dumps({
        "project_done": False,
        "reason": "needs work",
        "new_tasks": [_impact_task()],
    })
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert len(v.new_tasks) == 1
    assert v.new_tasks[0].title == "fix tests"
    assert "pytest" in v.new_tasks[0].objective
    assert v.new_tasks[0].impact_score == 4
    assert v.new_tasks[0].scope == "bounded"


def test_parse_planner_preserves_final_submission_scope():
    text = json.dumps({
        "project_done": False,
        "reason": "needs final proof",
        "new_tasks": [
            _impact_task(
                "prove readiness",
                "run validate-full-emnlp and fix blockers",
                impact_score=5,
                impact_area="requirement_gap",
                evidence="all bounded blockers appear resolved",
                scope="final_submission",
            )
        ],
    })
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert v.new_tasks[0].scope == "final_submission"


def test_critic_prompt_has_scoped_final_submission_gate() -> None:
    runner = _FakeRunner('{"stop": true, "reason": "ok", "improvements": []}')
    Critic(runner).evaluate(
        original_objective=(
            "## Backlog item metadata\n- planner_scope: final_submission\n\n"
            "Prepare the EMNLP submission package"
        ),
        latest_completion_summary="validate-pipeline passed",
        cycles_done=0,
        cycles_max=6,
        budget_remaining_usd=10.0,
    )
    sent_prompt, _ = runner.calls[0]
    assert "planner_scope: final_submission" in sent_prompt
    assert "Validator toolbelt (critic)" in sent_prompt
    assert "validate-academic-language-review --project-root ." in sent_prompt
    assert "validate-full-emnlp --project-root ." in sent_prompt
    assert "Do NOT apply this" in sent_prompt
    assert "paper_optimization_task" in sent_prompt


def test_parse_planner_restart_request_without_tasks():
    text = (
        '{"project_done": false, "reason": "needs fresh daemon", '
        '"restart_daemon": true, '
        '"restart_reason": "daemon lifecycle code changed", '
        '"new_tasks": []}'
    )
    v = parse_planner_text(text)
    assert v.project_done is False
    assert v.restart_daemon is True
    assert v.restart_reason == "daemon lifecycle code changed"
    assert v.new_tasks == []
    assert not v.error


def test_parse_planner_caps_at_3_tasks():
    tasks = [_impact_task(f"task{i}", f"do thing {i}") for i in range(5)]
    text = json.dumps({"project_done": False, "reason": "lots to do", "new_tasks": tasks})
    v = parse_planner_text(text)
    assert v is not None
    assert len(v.new_tasks) == 3


def test_parse_planner_inconsistent_done_with_tasks():
    """project_done=True but tasks listed → schema violation, retry later."""
    text = json.dumps({
        "project_done": True,
        "reason": "done",
        "new_tasks": [_impact_task("x", "y")],
    })
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert v.new_tasks == []
    assert v.error
    assert "project_done=true" in v.error


def test_parse_planner_inconsistent_not_done_no_tasks():
    """project_done=False but no tasks → retry later, not done."""
    text = '{"project_done": false, "reason": "", "new_tasks": []}'
    v = parse_planner_text(text)
    assert v.project_done is False
    assert v.error
    assert "no concrete tasks" in v.error


def test_parse_planner_rejects_only_low_impact_tasks():
    text = json.dumps({
        "project_done": False,
        "reason": "minor cleanup",
        "new_tasks": [
            _impact_task(
                "rename helper",
                "rename a helper for clarity",
                impact_score=2,
                evidence="would be cleaner",
            )
        ],
    })
    v = parse_planner_text(text)
    assert v.project_done is False
    assert v.new_tasks == []
    assert v.error == "planner produced no high-impact tasks"


def test_parse_planner_empty_input():
    empty = parse_planner_text("")
    assert empty.project_done is False
    assert empty.error == "empty planner output"
    garbage = parse_planner_text("no json here")
    assert garbage.project_done is False
    assert garbage.error == "unparseable planner output"


def test_parse_planner_malformed_json_returns_error():
    text = '{"project_done": false, "reason": "x", "new_tasks": [}'
    v = parse_planner_text(text)
    assert v.project_done is False
    assert "unparseable" in v.error


def test_parse_planner_tolerates_markdown_fences():
    text = "```json\n" + json.dumps({
        "project_done": False,
        "reason": "more work",
        "new_tasks": [_impact_task("a", "b")],
    }) + "\n```"
    v = parse_planner_text(text)
    assert v.project_done is False
    assert len(v.new_tasks) == 1


def test_parse_planner_text_ignores_brace_heavy_prose() -> None:
    txt = (
        "planner notes: {not the verdict}\n"
        + json.dumps({
            "project_done": False,
            "reason": "needs one task",
            "new_tasks": [
                _impact_task(
                    "tighten budget",
                    "add a cache for remaining_today",
                    evidence="remaining budget scan is on a hot path",
                )
            ],
        })
        + "\n"
        "afterword {still prose}\n"
    )
    v = parse_planner_text(txt)
    assert v.project_done is False
    assert v.new_tasks[0].title == "tighten budget"


def test_planner_verdict_dataclass_is_frozen():
    v = PlannerVerdict(project_done=True, reason="done")
    try:
        v.project_done = False
    except Exception:
        return
    raise AssertionError("PlannerVerdict should be frozen")
