"""Regression coverage for localized Manager backend-result semantics."""

from __future__ import annotations

import json

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.manager import Manager
from argus_skill.manager._session_ops import _ManagerSession
from argus_skill.manager.domain_author import VerticalDecisionError

_FAST_DECISION = json.dumps(
    {
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "confidence": 0.99,
    }
)
_CODEX_WARNING = "failed to record rollout items: thread example not found"


class _Result:
    def __init__(
        self,
        message: str = _FAST_DECISION,
        *,
        exit_code: int = 0,
        turn_completed: bool = True,
        turn_failed: bool = False,
        fatal_error: str | None = None,
        stderr_lines: list[str] | None = None,
        thread_id: str = "thread-1",
    ) -> None:
        self.exit_code = exit_code
        self.turn_completed = turn_completed
        self.turn_failed = turn_failed
        self.fatal_error = fatal_error
        self.stderr_lines = list(stderr_lines or [])
        self.agent_messages = [message] if message else []
        self.last_agent_message = message
        self.thread_id = thread_id


class _Runner:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self.result


@pytest.mark.parametrize(
    "diagnostic",
    [
        pytest.param("provider diagnostic: cache cleanup delayed", id="benign"),
        pytest.param(_CODEX_WARNING, id="known-codex-warning"),
    ],
)
def test_success_with_stderr_proceeds_once_and_retains_diagnostic(
    tmp_path, diagnostic: str
) -> None:
    result = _Result(stderr_lines=[diagnostic])
    runner = _Runner(result)

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "fix the requested bug"
    )

    assert decision.vertical == "software"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-project-grounding",
    ]
    assert result.stderr_lines == [diagnostic]


def test_fatal_error_fails_even_with_completed_zero_exit(tmp_path) -> None:
    runner = _Runner(_Result(fatal_error="provider policy denied this turn"))

    with pytest.raises(VerticalDecisionError, match="provider policy denied"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical("fix it")

    assert len(runner.calls) == 1


def test_turn_failed_fails_even_with_zero_exit(tmp_path) -> None:
    runner = _Runner(
        _Result(
            turn_completed=False,
            turn_failed=True,
            stderr_lines=["structured turn failed"],
        )
    )

    with pytest.raises(VerticalDecisionError, match="structured turn failed"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical("fix it")

    assert len(runner.calls) == 1


def test_adapter_preserves_turn_failed_for_manager_consumers() -> None:
    backend = AgentCliBackend()
    translated = backend._translate_result(
        AgentRunResult(
            command=["codex"],
            exit_code=0,
            thread_id="thread-1",
            agent_messages=[_FAST_DECISION],
            turn_completed=False,
            turn_failed=True,
            fatal_error=None,
            stderr_lines=["structured turn failed without a fatal event"],
        )
    )

    assert translated.exit_code == 0
    assert translated.fatal_error == "structured turn failed without a fatal event"
    assert translated.stderr_lines == [
        "structured turn failed without a fatal event"
    ]


def test_adapter_preserves_turn_failed_when_fatal_normalizes_away() -> None:
    backend = AgentCliBackend()
    translated = backend._translate_result(
        AgentRunResult(
            command=["codex"],
            exit_code=0,
            thread_id="thread-1",
            agent_messages=[_FAST_DECISION],
            turn_completed=False,
            turn_failed=True,
            fatal_error="Reconnecting... 1/3",
        )
    )

    assert translated.exit_code == 0
    assert translated.fatal_error == "backend reported a failed turn"


def test_nonzero_exit_fails_and_reports_stderr_diagnostic(tmp_path) -> None:
    runner = _Runner(
        _Result(
            exit_code=2,
            turn_completed=False,
            stderr_lines=["process failed after startup"],
        )
    )

    with pytest.raises(VerticalDecisionError, match="process failed after startup"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical("fix it")

    assert len(runner.calls) == 1


def test_missing_required_output_still_fails(tmp_path) -> None:
    runner = _Runner(_Result(message=""))

    with pytest.raises(VerticalDecisionError, match="could not decide"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical("fix it")


def test_completed_success_does_not_retry_from_stderr_text(tmp_path) -> None:
    (tmp_path / ".manager_session.json").write_text(
        json.dumps({"thread_id": "existing-thread"}), encoding="utf-8"
    )
    runner = _Runner(
        _Result(
            message="valid response",
            stderr_lines=[
                "No session, task, or name matched during optional cleanup"
            ],
            thread_id="completed-thread",
        )
    )

    result = _ManagerSession(runner, tmp_path).run_exec(
        prompt="continue", options=None, run_label="manager"
    )

    assert len(runner.calls) == 1
    assert runner.calls[0]["resume_thread_id"] == "existing-thread"
    assert result.thread_id == "completed-thread"
