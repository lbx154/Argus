"""Unit tests for the Critic sub-agent: parsing + objective rendering.

These tests use canned text inputs only; the Critic class itself takes
a ``RunnerBackend`` and is exercised indirectly via the iteration-loop
integration test.
"""
from __future__ import annotations

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
    txt = (
        '{"stop": false, "reason": "missing edge cases", '
        '"improvements": [{"title": "add property test", "rationale": "covers neg amounts", "acceptance": "pytest passes"}]}'
    )
    v = parse_critic_text(txt)
    assert v is not None
    assert v.stop is False
    assert len(v.improvements) == 1
    imp = v.improvements[0]
    assert imp.title == "add property test"
    assert imp.acceptance == "pytest passes"


def test_parse_drops_improvement_missing_acceptance():
    txt = '{"stop": false, "reason": "x", "improvements": [{"title": "polish", "rationale": "y"}]}'
    v = parse_critic_text(txt)
    assert v is not None
    # No valid improvements → flipped to stop
    assert v.stop is True


def test_parse_caps_improvements_at_three():
    body = ",".join(
        '{"title": "t%d", "acceptance": "a%d"}' % (i, i) for i in range(5)
    )
    txt = '{"stop": false, "reason": "r", "improvements": [' + body + "]}"
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
        '{"stop": false, "reason": "needs one more pass", "improvements": '
        '[{"title": "add edge-case test", "rationale": "brace noise hid the JSON", '
        '"acceptance": "pytest -q tests/critic/test_critic.py"}]}\n'
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
        Improvement(title="add tests", rationale="coverage low", acceptance="pytest"),
        Improvement(title="handle err", rationale="", acceptance="raises ValueError"),
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

    def run_exec(self, *, prompt, resume_thread_id, options, run_label=""):
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
    cfg = CriticConfig(model="o4-mini", reasoning_effort="low")
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


def test_evaluate_passes_run_label_to_runner():
    """Regression: CodexRunnerBackend.run_exec REQUIRES run_label kwarg.

    Before this fix the critic crashed with
    ``TypeError: run_exec() missing 1 required keyword-only argument:
    'run_label'`` after the very first iteration cycle on a real codex
    backend (memory backend tolerated the omission).
    """
    runner = _FakeRunner('{"stop": true, "reason": "ok", "improvements": []}')

    captured: dict = {}
    real_run_exec = runner.run_exec

    def strict_run_exec(*, prompt, resume_thread_id, options, run_label):
        # Mirror the real backend signature: run_label is REQUIRED kw-only.
        captured["run_label"] = run_label
        return real_run_exec(
            prompt=prompt,
            resume_thread_id=resume_thread_id,
            options=options,
            run_label=run_label,
        )

    runner.run_exec = strict_run_exec
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
    text = '{"project_done": "false", "reason": "needs work", "new_tasks": [{"title": "fix tests", "objective": "run pytest and fix failures"}]}'
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert len(v.new_tasks) == 1


def test_parse_planner_new_tasks():
    text = (
        '{"project_done": false, "reason": "needs work", '
        '"new_tasks": [{"title": "fix tests", "objective": "run pytest and fix failures"}]}'
    )
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is False
    assert len(v.new_tasks) == 1
    assert v.new_tasks[0].title == "fix tests"
    assert "pytest" in v.new_tasks[0].objective


def test_parse_planner_caps_at_3_tasks():
    tasks = [
        {"title": f"task{i}", "objective": f"do thing {i}"}
        for i in range(5)
    ]
    import json
    text = json.dumps({"project_done": False, "reason": "lots to do", "new_tasks": tasks})
    v = parse_planner_text(text)
    assert v is not None
    assert len(v.new_tasks) == 3


def test_parse_planner_inconsistent_done_with_tasks():
    """project_done=True but tasks listed → honor done, discard tasks."""
    text = (
        '{"project_done": true, "reason": "done", '
        '"new_tasks": [{"title": "x", "objective": "y"}]}'
    )
    v = parse_planner_text(text)
    assert v is not None
    assert v.project_done is True
    assert v.new_tasks == []


def test_parse_planner_inconsistent_not_done_no_tasks():
    """project_done=False but no tasks → retry later, not done."""
    text = '{"project_done": false, "reason": "", "new_tasks": []}'
    v = parse_planner_text(text)
    assert v.project_done is False
    assert v.error
    assert "no concrete tasks" in v.error


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
    text = '```json\n{"project_done": false, "reason": "more work", "new_tasks": [{"title": "a", "objective": "b"}]}\n```'
    v = parse_planner_text(text)
    assert v.project_done is False
    assert len(v.new_tasks) == 1


def test_parse_planner_text_ignores_brace_heavy_prose() -> None:
    txt = (
        "planner notes: {not the verdict}\n"
        '{"project_done": false, "reason": "needs one task", "new_tasks": '
        '[{"title": "tighten budget", "objective": "add a cache for remaining_today"}]}\n'
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
