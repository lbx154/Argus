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
