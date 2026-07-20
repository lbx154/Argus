"""F4 regression: the engineer's final message is rendered into the reviewer
prompt exactly ONCE.

Before the fix, ``engineer/runner.py`` fed the SAME ``engineer_message`` string
to both ``main_summary`` and ``engineer_reasoning_summary``, so it was echoed
twice in the reviewer prompt — under "Main agent last summary" AND under
"- engineer_reasoning_summary:" — duplicating input tokens on every reviewer
round. The engineer's full reasoning/process already reaches the reviewer via
the ``engineer_log_path`` audit block, so the duplicate field carried zero
incremental signal.
"""
from __future__ import annotations

import inspect

from argus_skill.reviewer import Reviewer, ReviewerConfig


class _DeadResult:
    """A RunnerResult-shaped object; the decision is irrelevant to these tests —
    we only assert on the prompt captured *before* run_exec returns."""

    agent_messages: list[str] = []
    exit_code = 1
    fatal_error = "Process exited with code 1 before turn completion."
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0


class _CapturingRunner:
    def __init__(self) -> None:
        self.prompt: str | None = None

    def run_exec(self, *, prompt, **_kwargs):
        self.prompt = prompt
        return _DeadResult()


_MARKER = "ENGINEER_FINAL_MARKER_42abc"


def _capture_prompt(*, round_max: int | None = 3) -> str:
    runner = _CapturingRunner()
    kwargs = dict(
        objective="minimize val_bpb",
        round_index=1,
        session_id=None,
        main_summary=f"engineer done: {_MARKER}",
        main_error=None,
        config=ReviewerConfig(model="gpt-5.5"),
        active_skill_id="some_skill",
        prev_review_summary="prior review notes",
    )
    if round_max is not None:
        kwargs["round_max"] = round_max
    Reviewer(runner=runner).evaluate(**kwargs)
    assert runner.prompt is not None
    return runner.prompt


def test_engineer_final_message_rendered_once() -> None:
    prompt = _capture_prompt()
    assert prompt.count(_MARKER) == 1


def test_engineer_reasoning_summary_label_is_gone() -> None:
    prompt = _capture_prompt()
    assert "engineer_reasoning_summary" not in prompt


def test_prev_review_and_skill_still_rendered_once() -> None:
    prompt = _capture_prompt()
    assert prompt.count("previous_review_summary") == 1
    assert "prior review notes" in prompt
    assert "some_skill" in prompt


def test_reviewer_sees_round_budget() -> None:
    assert "Round: 1/3" in _capture_prompt()


def test_reviewer_round_budget_fallback_omits_denominator() -> None:
    prompt = _capture_prompt(round_max=None)
    assert "Round: 1\n" in prompt
    assert "Round: 1/0" not in prompt


def test_evaluate_signature_drops_reasoning_summary() -> None:
    params = inspect.signature(Reviewer.evaluate).parameters
    assert "engineer_reasoning_summary" not in params
    # The genuinely distinct channels remain.
    assert "main_summary" in params
    assert "prev_review_summary" in params
