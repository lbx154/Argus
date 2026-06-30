"""Regression test: the engineer/runner.py SupervisedEngineer.run() loop
must emit ``round.main.completed`` carrying the engineer call's
``input_tokens`` / ``output_tokens``, and the engineer/reviewer.py
``Reviewer.evaluate()`` must populate the same fields on
``ReviewDecision``.

Both fields feed the LifeSupervisor's ``_CostTrackingSink`` which is
what enforces ``iteration_budget_usd``. Pre-fix, the cost sink only
ever saw the reviewer half (zeroed) and engineers got billed at $0,
silently breaking iteration budget enforcement.

Citations:
- argus_skill/engineer/runner.py — emits ``round.main.completed``
- argus_skill/engineer/reviewer.py — sets input/output_tokens on every
  ReviewDecision return path
- argus_skill/life/supervisor.py:166 — ``_CostTrackingSink.handle_event``
  reads the two fields from these events
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from argus_skill.core.models import (
    ReviewDecision,
    RunnerResult,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)


class _TokenedEngineer:
    """Engineer runner that returns deterministic token counts."""

    def __init__(self, in_tok: int, out_tok: int) -> None:
        self._in = in_tok
        self._out = out_tok
        self.calls = 0

    def run_exec(self, **kwargs):  # noqa: D401
        self.calls += 1
        return RunnerResult(
            exit_code=0,
            agent_messages=[f"engineer-r{self.calls}: did concrete work running pytest -q"],
            input_tokens=self._in,
            cached_input_tokens=self._in // 10,
            output_tokens=self._out,
        )


class _DoneReviewerWithTokens:
    """Reviewer stub that fakes a 'done' verdict and propagates tokens."""

    def __init__(self, in_tok: int, out_tok: int) -> None:
        self._in = in_tok
        self._out = out_tok

    def evaluate(self, **_kwargs):
        return ReviewDecision(
            status="done",
            reason="ok",
            next_action="",
            round_summary_markdown="",
            completion_summary_markdown="",
            input_tokens=self._in,
            output_tokens=self._out,
        )


def _make_supervised(eng: _TokenedEngineer, rev) -> SupervisedEngineer:
    se = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    se.engineer_runner = eng
    se.engineer_config = EngineerConfig(model="stub")
    se.reviewer = rev
    se.reviewer_config = ReviewerConfig(model="stub")
    return cast(SupervisedEngineer, se)


def test_round_main_completed_emitted_with_engineer_tokens(tmp_path: Path) -> None:
    eng = _TokenedEngineer(in_tok=12000, out_tok=345)
    rev = _DoneReviewerWithTokens(in_tok=200, out_tok=50)
    se = _make_supervised(eng, rev)

    events: list[dict] = []
    se.run(
        objective="demo",
        engineer_prompt_builder=lambda na, _include_static=True: "PROMPT",
        supervised_config=SupervisedConfig(max_rounds=1, check_commands=[]),
        workdir=tmp_path,
        on_event=events.append,
    )

    main_evts = [e for e in events if e.get("type") == "round.main.completed"]
    round_start_evts = [e for e in events if e.get("type") == "round.start"]
    review_start_evts = [e for e in events if e.get("type") == "round.review.started"]
    review_evts = [e for e in events if e.get("type") == "round.review.completed"]

    assert round_start_evts[0]["round_max"] == 1
    assert len(main_evts) == 1, (
        "expected exactly one round.main.completed per engineer round; got: "
        + repr([e.get("type") for e in events])
    )
    assert main_evts[0]["round_max"] == 1
    assert main_evts[0]["input_tokens"] == 12000
    assert main_evts[0]["cached_input_tokens"] == 1200
    assert main_evts[0]["output_tokens"] == 345

    assert review_start_evts[0]["round_max"] == 1
    assert len(review_evts) == 1
    assert review_evts[0]["round_max"] == 1
    assert review_evts[0]["input_tokens"] == 200
    assert review_evts[0]["output_tokens"] == 50


# ---------------------------------------------------------------------------
# Reviewer-side: every return path must set input_tokens / output_tokens
# ---------------------------------------------------------------------------


class _StubReviewerRunner:
    def __init__(
        self,
        agent_messages,
        in_tok=999,
        out_tok=11,
        exit_code=0,
        fatal_error=None,
    ) -> None:
        self.agent_messages = agent_messages
        self._in = in_tok
        self._out = out_tok
        self._exit_code = exit_code
        self._fatal_error = fatal_error

    def run_exec(self, **kwargs):  # noqa: D401
        return RunnerResult(
            exit_code=self._exit_code,
            agent_messages=list(self.agent_messages),
            fatal_error=self._fatal_error,
            input_tokens=self._in,
            cached_input_tokens=self._in // 10,
            output_tokens=self._out,
        )


class _ExplodingReviewerRunner:
    def run_exec(self, **kwargs):  # noqa: D401, ANN001
        raise RuntimeError("reviewer subprocess disappeared")


def test_reviewer_propagates_tokens_on_empty_messages() -> None:
    runner = _StubReviewerRunner(agent_messages=[], in_tok=42, out_tok=7)
    rev = Reviewer(runner)
    decision = rev.evaluate(
        objective="demo",
        round_index=1,
        session_id=None,
        main_summary="ran pytest -q",
        main_error=None,
        checks=[],
        config=ReviewerConfig(model="stub"),
    )
    assert decision.input_tokens == 42
    assert decision.cached_input_tokens == 4
    assert decision.output_tokens == 7


def test_reviewer_empty_backend_failure_is_environmental_block() -> None:
    # Contract change (2026-06-25): a reviewer backend failure that renders NO
    # verdict must be a loud, non-verdict ``blocked`` carrying the explicit
    # ``backend_unavailable`` marker — NOT a soft ``continue``. The old
    # ``continue`` let a *persistent* failure run the completion gate blind for
    # ~1.5h. "Don't kill the mission on a *transient* blip" still holds, but that
    # tolerance now lives in the supervised loop (streak + backoff retry, then
    # escalate) — see tests/test_reviewer_backend_failure_escalates.py. Token
    # propagation on the failure path is unchanged.
    runner = _StubReviewerRunner(
        agent_messages=[],
        in_tok=42,
        out_tok=7,
        exit_code=0,
        fatal_error="stream disconnected before completion: response.failed event received",
    )
    rev = Reviewer(runner)
    decision = rev.evaluate(
        objective="demo",
        round_index=1,
        session_id=None,
        main_summary="engineer backend recovered",
        main_error=None,
        checks=[],
        config=ReviewerConfig(model="stub"),
    )

    assert decision.status == "blocked"
    assert decision.backend_unavailable is True
    assert decision.failure_cause == "environmental"
    assert "response.failed" in decision.reason
    assert decision.input_tokens == 42
    assert decision.output_tokens == 7


def test_reviewer_propagates_tokens_on_unparseable_output() -> None:
    runner = _StubReviewerRunner(
        agent_messages=["this is not json at all"],
        in_tok=33,
        out_tok=4,
    )
    rev = Reviewer(runner)
    decision = rev.evaluate(
        objective="demo",
        round_index=1,
        session_id=None,
        main_summary="ran pytest -q",
        main_error=None,
        checks=[],
        config=ReviewerConfig(model="stub"),
    )
    assert decision.input_tokens == 33
    assert decision.cached_input_tokens == 3
    assert decision.output_tokens == 4


def test_reviewer_propagates_tokens_on_valid_json() -> None:
    runner = _StubReviewerRunner(
        agent_messages=[
            '{"status":"done","reason":"ok","next_action":""}'
        ],
        in_tok=77,
        out_tok=9,
    )
    rev = Reviewer(runner)
    decision = rev.evaluate(
        objective="demo",
        round_index=1,
        session_id=None,
        main_summary="ran pytest -q and added concrete code in src/foo.py",
        main_error=None,
        checks=[],
        config=ReviewerConfig(model="stub"),
    )
    assert decision.input_tokens == 77
    assert decision.cached_input_tokens == 7
    assert decision.output_tokens == 9


def test_reviewer_runner_exception_returns_blocked_decision() -> None:
    rev = Reviewer(_ExplodingReviewerRunner())
    decision = rev.evaluate(
        objective="demo",
        round_index=1,
        session_id=None,
        main_summary="engineer summary",
        main_error=None,
        checks=[],
        config=ReviewerConfig(model="stub"),
    )

    assert decision.status == "blocked"
    assert "RuntimeError: reviewer subprocess disappeared" in decision.reason
    assert decision.failure_cause == "environmental"
