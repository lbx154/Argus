"""The round loop's answer to a call that used its whole provider-turn
allowance: keep the work, ask the same conversation to write the checkpoint
and a summary, then continue the task in a fresh session — with a plain-words
event on the record, because the allowance is housekeeping, not an error.

Companion to ``tests/agent_cli/test_provider_turn_cap.py`` (which pins the
runner-side counting/wind-down); these tests pin the harness-side restart in
``engineer/round_execution.py`` and the reviewer leg in
``engineer/round_reviewer.py``. No real backend, no network, no spend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from argus_skill.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import ReviewerConfig

_CAP_RECEIPT = (
    "Provider turn cap reached: this engineer-r1 call used 40 provider turns "
    "(allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP). Each further turn would "
    "resend the whole grown transcript; the harness continues this work in a "
    "fresh session instead."
)


def _make_supervised(engineer, reviewer) -> SupervisedEngineer:
    supervised = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    supervised.engineer_runner = engineer
    supervised.engineer_config = EngineerConfig(model="stub")
    supervised.reviewer = reviewer
    supervised.reviewer_config = ReviewerConfig(model="stub")
    return cast(SupervisedEngineer, supervised)


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        return ReviewDecision(status="done", reason="the work holds", next_action="")


class _CappedThenFinishingEngineer:
    """First call ends at the allowance; the wind-down and round 2 succeed."""

    def __init__(self, stderr_history: str | None = None) -> None:
        self.calls: list[tuple[str, str | None, str]] = []
        self.stderr_history = stderr_history

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        self.calls.append((run_label, resume_thread_id, prompt))
        if run_label == "engineer-r1":
            if self.stderr_history is not None:
                return translate_result(
                    SimpleNamespace(
                        exit_code=-15, fatal_error=_CAP_RECEIPT, turn_failed=True,
                        agent_messages=["ran the first four ablations"],
                        thread_id="thr-capped", stdout_lines=[],
                        stderr_lines=[self.stderr_history],
                        json_events=[{"type": "turn.completed", "usage": {
                            "input_tokens": 1000, "output_tokens": 100,
                        }}],
                    ),
                    resume_thread_id=resume_thread_id, copilot_usage=None,
                    usage_accumulator=UsageAccumulator(),
                )
            return RunnerResult(
                exit_code=-15,
                agent_messages=["ran the first four ablations"],
                thread_id="thr-capped",
                fatal_error=_CAP_RECEIPT,
                stop_kind="backend_unavailable",
                input_tokens=1000,
                output_tokens=100,
            )
        if run_label == "engineer-r1.winddown":
            return RunnerResult(
                exit_code=0,
                agent_messages=[
                    "Checkpoint written. Done: ablations 1-4. In flight: none. "
                    "Next: run ablation 5 and update the table."
                ],
                thread_id="thr-capped",
                input_tokens=50,
                output_tokens=20,
            )
        return RunnerResult(
            exit_code=0,
            agent_messages=["ablation 5 finished; table updated"],
            thread_id="thr-fresh",
        )


@pytest.mark.parametrize("stderr_history", [
    None,
    "2026-09-07T04:28:00.401010Z WARN expected ordinal 383, got 382",
    "Reconnecting... 1/3 (HTTP 401 Unauthorized)",
    "ERROR: Failed to load models: HTTP 503 Service Unavailable",
])
def test_capped_call_winds_down_and_continues_in_a_fresh_session(
    tmp_path: Path, stderr_history: str | None,
) -> None:
    engineer = _CappedThenFinishingEngineer(stderr_history)
    reviewer = _DoneReviewer()
    supervised = _make_supervised(engineer, reviewer)
    checkpoint = tmp_path / "CHECKPOINT.md"
    checkpoint.write_text("# checkpoint\n", encoding="utf-8")

    events: list[dict] = []
    prompts_seen: list[tuple[str | None, bool]] = []

    def _prompt_builder(next_action, include_static=True):
        prompts_seen.append((next_action, include_static))
        return f"WORK\n{next_action or ''}"

    status, rounds, _final, _reason, _thread = supervised.run(
        objective="finish the ablation study",
        engineer_prompt_builder=_prompt_builder,
        supervised_config=SupervisedConfig(
            max_rounds=4,
            checkpoint_path=checkpoint,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    labels = [label for label, _tid, _prompt in engineer.calls]
    assert labels == ["engineer-r1", "engineer-r1.winddown", "engineer-r2"]

    # The wind-down resumed the very conversation that used its allowance and
    # asked for the checkpoint plus a summary.
    winddown_label, winddown_tid, winddown_prompt = engineer.calls[1]
    assert winddown_tid == "thr-capped"
    assert "CHECKPOINT.md" in winddown_prompt or str(checkpoint) in winddown_prompt
    assert "summary" in winddown_prompt.lower()
    assert "do not start new work" in winddown_prompt.lower()

    # The fresh session starts over (no resumed thread) and its prompt carries
    # the wind-down summary forward.
    _r2_label, r2_tid, _r2_prompt = engineer.calls[2]
    assert r2_tid is None
    next_action_for_round_2 = prompts_seen[1][0] or ""
    assert "ablation 5" in next_action_for_round_2
    assert "allowance" in next_action_for_round_2

    # A plain-words event marks the restart as housekeeping, not an error.
    restarts = [
        event for event in events
        if event.get("type") == "round.provider_turn_cap.restart"
    ]
    assert len(restarts) == 1
    assert restarts[0]["streak"] == 1
    assert restarts[0]["checkpoint_available"] is True
    assert "not an error" in restarts[0]["text"]
    assert restarts[0]["input_tokens"] == 50  # the wind-down call's own usage

    # The recorded round keeps the work with a continue judgment, and no
    # backend-failure accounting ran.
    assert rounds[0].review.status == "continue"
    assert "allowance" in rounds[0].review.reason
    assert not any(
        event.get("type") == "round.watchdog.retry" for event in events
    )


class _AlwaysCappedEngineer:
    """Every call ends at the allowance and leaves no resumable thread."""

    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        self.calls += 1
        return RunnerResult(
            exit_code=-15,
            agent_messages=[f"partial work, attempt {self.calls}"],
            thread_id=None,
            fatal_error=_CAP_RECEIPT,
            stop_kind="backend_unavailable",
        )


def test_a_run_of_capped_calls_stops_the_mission_truthfully(
    tmp_path: Path,
) -> None:
    engineer = _AlwaysCappedEngineer()
    supervised = _make_supervised(engineer, _DoneReviewer())

    events: list[dict] = []
    status, rounds, _final, reason, _thread = supervised.run(
        objective="a task that never converges",
        engineer_prompt_builder=lambda _na, _include_static=True: "WORK",
        supervised_config=SupervisedConfig(
            max_rounds=10,
            provider_turn_cap_streak_limit=3,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "error"
    assert engineer.calls == 3  # no wind-down without a thread id
    assert len(rounds) == 3
    assert "allowance" in reason
    assert "ARGUS_SKILL_PROVIDER_TURN_CAP" in reason
    restarts = [
        event for event in events
        if event.get("type") == "round.provider_turn_cap.restart"
    ]
    assert [event["streak"] for event in restarts] == [1, 2, 3]


@pytest.mark.parametrize("stop_kind,expected", [
    (None, "done"),
    ("budget_exhausted", "paused_budget"),
    ("operator_abort", "aborted"),
])
def test_final_review_rotates_beyond_three_sessions_and_keeps_real_stops(
    tmp_path: Path, stop_kind: str | None, expected: str,
) -> None:
    from argus_skill.core.venue_review import configure_venue_revisions

    class LongRepair(_AlwaysCappedEngineer):
        def run_exec(self, **kwargs):
            if self.calls < 4:
                return super().run_exec(**kwargs)
            self.calls += 1
            if stop_kind:
                return RunnerResult(exit_code=1, fatal_error="Explicit stop", stop_kind=stop_kind)
            return RunnerResult(exit_code=0, agent_messages=["PPT repair is ready for independent review."])

    engineer = LongRepair()
    reviewer = _DoneReviewer()
    supervised = _make_supervised(engineer, reviewer)
    config = SupervisedConfig(
        checkpoint_path=tmp_path / "CHECKPOINT.md", background_subagent_advisory=False,
    )
    config.checkpoint_path.write_text("Continue the native PPT mathematical-label repair.")
    configure_venue_revisions(config)
    events: list[dict] = []
    guidance: list[str] = []

    def prompt(next_action, include_static=True):
        guidance.append(next_action or "")
        return "Continue the current final Review. " + (next_action or "")

    status, rounds, _final, _reason, _thread = supervised.run(
        objective="Repair this paper's figure in final Review",
        engineer_prompt_builder=prompt, supervised_config=config,
        workdir=tmp_path, on_event=events.append,
    )

    assert status == expected
    assert engineer.calls == 5
    assert reviewer.calls == (1 if stop_kind is None else 0)
    assert all(round.review.status == "continue" for round in rounds[:4])
    assert "partial work, attempt 4" in guidance[4]
    restarts = [event for event in events if event.get("type") == "round.provider_turn_cap.restart"]
    assert [event["streak"] for event in restarts] == [1, 2, 3, 4]
    assert all(event["streak_limit"] == 0 for event in restarts)
    assert all(event["checkpoint_available"] for event in restarts)


class _SteadyEngineer:
    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        return RunnerResult(
            exit_code=0,
            agent_messages=["did the work; evidence under results/"],
        )


class _CappedOnceReviewer:
    """First review ends at the allowance; the fresh retry reaches a judgment."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return ReviewDecision(
                status="blocked",
                reason="review ended at the per-call provider-turn allowance",
                next_action="",
                backend_unavailable=True,
                backend_fatal_error=_CAP_RECEIPT.replace(
                    "engineer-r1", "reviewer"
                ),
                backend_exit_code=-15,
                backend_stop_kind="backend_unavailable",
            )
        return ReviewDecision(status="done", reason="the work holds", next_action="")


def test_capped_reviewer_call_restarts_once_without_failure_accounting(
    tmp_path: Path,
) -> None:
    reviewer = _CappedOnceReviewer()
    supervised = _make_supervised(_SteadyEngineer(), reviewer)

    events: list[dict] = []
    status, _rounds, _final, _reason, _thread = supervised.run(
        objective="check the ablation study",
        engineer_prompt_builder=lambda _na, _include_static=True: "WORK",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert reviewer.calls == 2
    restarts = [
        event for event in events
        if event.get("type") == "round.provider_turn_cap.reviewer_restart"
    ]
    assert len(restarts) == 1
    assert "not an error" in restarts[0]["text"]
    # The routine restart never touched the reviewer backend-failure streak.
    assert not any(
        event.get("type") == "round.reviewer_backend_failure"
        for event in events
    )
