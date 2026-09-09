"""An ended native-shell wait resumes work without buying a premature review."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from argus_skill.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus_skill.core.models import ReviewDecision, RunnerResult
from argus_skill.core.runner_receipts import PROVIDER_BACKGROUND_WAIT_RECEIPT
from argus_skill.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus_skill.reviewer import ReviewerConfig


def _waiting_result():
    return translate_result(
        SimpleNamespace(
            exit_code=0, turn_failed=True, fatal_error=PROVIDER_BACKGROUND_WAIT_RECEIPT,
            agent_messages=["Earlier progress", ""], thread_id="native-wait",
            stdout_lines=[], stderr_lines=["Recovered request: HTTP 401 Unauthorized"],
            json_events=[],
        ),
        resume_thread_id=None, copilot_usage=None, usage_accumulator=UsageAccumulator(),
    )


class _Reviewer:
    def __init__(self):
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ReviewDecision(status="continue", reason="Useful first batch.", next_action="Repair the remaining baseline.")
        return ReviewDecision(status="done", reason="The complete comparison holds.", next_action="")


def _supervised(engineer, reviewer):
    value = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    value.engineer_runner = engineer
    value.engineer_config = EngineerConfig(model="stub")
    value.reviewer = reviewer
    value.reviewer_config = ReviewerConfig(model="stub")
    return value


def test_repeated_waits_preserve_reviewer_feedback_without_failure_backoff(tmp_path):
    class Engineer:
        def __init__(self):
            self.prompts = []

        def run_exec(self, *, prompt, **kwargs):
            self.prompts.append(prompt)
            if 2 <= len(self.prompts) <= 5:
                return _waiting_result()
            return RunnerResult(exit_code=0, agent_messages=["A scientific batch is complete."])

    engineer = Engineer()
    reviewer = _Reviewer()
    events = []
    status, rounds, *_ = _supervised(engineer, reviewer).run(
        objective="Complete the comparison",
        engineer_prompt_builder=lambda action, include_static=True: action or "Begin",
        supervised_config=SupervisedConfig(
            max_rounds=8, backend_failure_threshold=1, background_subagent_advisory=False,
        ),
        workdir=tmp_path, on_event=events.append,
    )

    assert status == "done"
    assert len(engineer.prompts) == 6 and reviewer.calls == 2
    assert sum(r.review.review_source == "provider_background_wait" for r in rounds) == 4
    assert engineer.prompts[-1].count("Repair the remaining baseline.") == 1
    assert engineer.prompts[-1].count("Argus durable job interface") == 1
    assert not any("backoff" in e.get("type", "") for e in events)
    assert all(not r.engineer_message for r in rounds[1:5])


def test_a_working_provider_wait_breaks_a_backend_failure_streak(tmp_path):
    class Engineer:
        calls = 0

        def run_exec(self, **kwargs):
            self.calls += 1
            if self.calls in (1, 3):
                return RunnerResult(
                    exit_code=1, stop_kind="transient_error",
                    fatal_error="connection reset" if self.calls == 1 else "service unavailable",
                )
            if self.calls == 2:
                return _waiting_result()
            return RunnerResult(exit_code=0, agent_messages=["Complete comparison."])

    engineer = Engineer()
    reviewer = _Reviewer()
    reviewer.calls = 1
    status, *_ = _supervised(engineer, reviewer).run(
        objective="Complete the task",
        engineer_prompt_builder=lambda action, include_static=True: action or "Begin",
        supervised_config=SupervisedConfig(
            max_rounds=5, backend_failure_threshold=2,
            backend_failure_backoff_seconds=0, background_subagent_advisory=False,
        ),
        workdir=tmp_path,
    )
    assert status == "done"
    assert engineer.calls == 4 and reviewer.calls == 2


@pytest.mark.parametrize("stop_kind,expected", [
    ("operator_abort", "aborted"),
    ("daemon_shutdown", "paused_daemon_shutdown"),
    ("budget_exhausted", "paused_budget"),
])
def test_real_stops_still_take_precedence_after_a_wait(tmp_path, stop_kind, expected):
    class Engineer:
        calls = 0

        def run_exec(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _waiting_result()
            return RunnerResult(exit_code=0, agent_messages=[], stop_kind=stop_kind)

    engineer = Engineer()
    reviewer = _Reviewer()
    status, *_ = _supervised(engineer, reviewer).run(
        objective="Continue work",
        engineer_prompt_builder=lambda action, include_static=True: action or "Begin",
        supervised_config=SupervisedConfig(max_rounds=5, background_subagent_advisory=False),
        workdir=tmp_path,
    )
    assert status == expected
    assert engineer.calls == 2 and reviewer.calls == 0
