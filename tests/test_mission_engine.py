from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from argus_skill.core.models import CheckResult, ReviewDecision, RunnerResult
from argus_skill.mission.engine import MissionLoopConfig, MissionLoopEngine


class _RecordingRunner:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.calls: list[dict[str, Any]] = []
        self.config = SimpleNamespace(skill_name=None)

    def run_exec(self, **kwargs) -> RunnerResult:
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        return RunnerResult(
            exit_code=0,
            agent_messages=[self._messages[index]],
            thread_id=f"thread-{index + 1}",
            fatal_error=None,
        )


class _ScriptedRunner:
    def __init__(self, results: list[RunnerResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.config = SimpleNamespace(skill_name=None)

    def run_exec(self, **kwargs) -> RunnerResult:
        self.calls.append(kwargs)
        return self._results.pop(0)


class _DoneReviewer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs) -> ReviewDecision:
        self.calls.append(kwargs)
        return ReviewDecision(
            status="done",
            confidence=0.99,
            reason="clean",
            next_action="stop",
            round_summary_markdown="checks passed",
            completion_summary_markdown="done",
        )


class _ChecksAwareReviewer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs) -> ReviewDecision:
        self.calls.append(kwargs)
        checks = kwargs["checks"]
        if any(not check.passed for check in checks):
            return ReviewDecision(
                status="continue",
                confidence=0.1,
                reason="failed checks",
                next_action="fix the failing checks",
                round_summary_markdown="checks failed",
                completion_summary_markdown="",
            )
        return ReviewDecision(
            status="done",
            confidence=0.99,
            reason="clean",
            next_action="stop",
            round_summary_markdown="checks passed",
            completion_summary_markdown="done",
        )


class _SummaryReviewer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, **kwargs) -> ReviewDecision:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return ReviewDecision(
                status="continue",
                confidence=0.4,
                reason="round one reason",
                next_action="keep going",
                round_summary_markdown="# round one summary",
                completion_summary_markdown="",
            )
        return ReviewDecision(
            status="done",
            confidence=0.95,
            reason="round two done",
            next_action="stop",
            round_summary_markdown="# round two summary",
            completion_summary_markdown="done",
        )


def test_check_commands_reach_reviewer_and_block_false_clean_completion(
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], int, str | None]] = []

    def fake_run_checks(
        commands: list[str],
        timeout_seconds: int,
        *,
        cwd: str | None = None,
    ) -> list[CheckResult]:
        calls.append((list(commands), timeout_seconds, cwd))
        return [
            CheckResult(
                command=commands[0],
                exit_code=1,
                passed=False,
                output_tail="boom",
            )
        ]

    monkeypatch.setattr("argus_skill.mission.engine.run_checks", fake_run_checks)

    runner = _RecordingRunner(["looks clean to me"])
    reviewer = _ChecksAwareReviewer()
    engine = MissionLoopEngine(
        runner=runner,
        reviewer=reviewer,
        planner=None,
        config=MissionLoopConfig(
            objective="demo",
            max_rounds=1,
            check_commands=["pytest -q"],
            mission_id="mission-1",
        ),
        state_store=object(),
    )

    result = engine.run()

    assert calls == [(["pytest -q"], 600, None)]
    assert len(reviewer.calls) == 1
    assert len(reviewer.calls[0]["checks"]) == 1
    assert reviewer.calls[0]["checks"][0].passed is False
    assert result.status == "max_rounds"
    assert result.rounds[0].review.status == "continue"


def test_round_two_receives_previous_review_summary() -> None:
    runner = _RecordingRunner(["round one", "round two"])
    reviewer = _SummaryReviewer()
    engine = MissionLoopEngine(
        runner=runner,
        reviewer=reviewer,
        planner=None,
        config=MissionLoopConfig(
            objective="demo",
            max_rounds=2,
            check_commands=[],
            mission_id="mission-1",
        ),
        state_store=object(),
    )

    result = engine.run()

    assert result.status == "done"
    assert len(reviewer.calls) == 2
    assert reviewer.calls[0]["prev_review_summary"] == ""
    assert reviewer.calls[1]["prev_review_summary"] == "# round one summary"


def test_mission_engine_backend_failure_skips_reviewer_and_retries_fresh() -> None:
    runner = _ScriptedRunner([
        RunnerResult(
            exit_code=0,
            thread_id="poison-thread",
            fatal_error=(
                "Reconnecting... 100/100 "
                "(stream disconnected before completion: response.failed event received)"
            ),
        ),
        RunnerResult(
            exit_code=0,
            agent_messages=["recovered"],
            thread_id="healthy-thread",
        ),
    ])
    reviewer = _DoneReviewer()
    events: list[dict[str, Any]] = []
    engine = MissionLoopEngine(
        runner=runner,
        reviewer=reviewer,
        planner=None,
        config=MissionLoopConfig(
            objective="demo",
            max_rounds=2,
            mission_id="mission-1",
            backend_failure_backoff_seconds=0,
        ),
        state_store=object(),
        event_sink=events.append,
    )

    result = engine.run()

    assert result.status == "done"
    assert result.last_thread_id == "healthy-thread"
    assert len(reviewer.calls) == 1
    assert runner.calls[0]["resume_thread_id"] is None
    assert runner.calls[1]["resume_thread_id"] is None
    skipped = [event for event in events if event.get("review_skipped")]
    assert len(skipped) == 1
    assert skipped[0]["status"] == "continue"


def test_mission_engine_repeated_backend_failures_escalate_to_error() -> None:
    runner = _ScriptedRunner([
        RunnerResult(
            exit_code=0,
            thread_id="bad-1",
            fatal_error="429 Too Many Requests",
        ),
        RunnerResult(
            exit_code=0,
            thread_id="bad-2",
            fatal_error=(
                "Reconnecting... 100/100 "
                "(stream disconnected before completion: response.failed event received)"
            ),
        ),
    ])
    reviewer = _DoneReviewer()
    engine = MissionLoopEngine(
        runner=runner,
        reviewer=reviewer,
        planner=None,
        config=MissionLoopConfig(
            objective="demo",
            max_rounds=3,
            mission_id="mission-1",
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        state_store=object(),
    )

    result = engine.run()

    assert result.status == "error"
    assert len(result.rounds) == 2
    assert reviewer.calls == []
    assert result.last_thread_id is None
    assert "backend_failure_streak=2/2" in result.reason
