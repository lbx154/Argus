"""A turn is never stopped on a timer by default; a stopped turn names its command so it is not repeated as it was."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.agent_cli._idle_watchdog import running_tool_after_event
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_stop_signals import (
    fatal_error_is_idle_termination,
    idle_termination_review_decision,
    idle_termination_running_tool,
)

REASON = (
    "Forced restart after hard idle timeout (1800s without progress; "
    "running tool: find / -name '*pdftoppm*')"
)


def test_engineer_turns_have_no_hard_stop(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS", raising=False)
    assert not hasattr(SupervisedConfig(), "runner_hard_idle_seconds")
    assert SupervisedConfig().runner_stalled_idle_seconds == 1800


def test_the_stopped_turn_names_the_command_and_says_what_to_do_instead() -> None:
    assert fatal_error_is_idle_termination(REASON)
    assert not fatal_error_is_idle_termination("Process exited with code 1 before turn completion.")
    assert idle_termination_running_tool(REASON) == "find / -name '*pdftoppm*'"
    assert idle_termination_running_tool("Forced restart after hard idle timeout (900s without a model stream event).") == ""

    decision = idle_termination_review_decision(fatal_error=REASON, exit_code=-15, streak=1, threshold=2)
    assert decision.status == "continue"
    assert "find / -name '*pdftoppm*'" in decision.reason
    assert "model service" not in decision.reason
    assert "timeout" in decision.next_action and "background job" in decision.next_action
    assert "CHECKPOINT.md" in decision.next_action
    # next_action is what reaches the next Engineer prompt, so it names the command too.
    assert "find / -name '*pdftoppm*'" in decision.next_action


def test_running_tool_keeps_commands_that_end_in_a_parenthesis() -> None:
    reason = (
        "Forced restart after hard idle timeout (1800s without progress; "
        "running tool: echo $(date))"
    )
    assert idle_termination_running_tool(reason) == "echo $(date)"


def _capture_engineer_options(monkeypatch, **config):
    from argus.core.models import RunnerResult
    from argus.engineer import runner as runner_module

    captured: list = []

    def fake_run_exec(_runner, *, prompt, options, **_kwargs):
        captured.append(options)
        return RunnerResult(exit_code=0, agent_messages=["ok"])

    monkeypatch.setattr(runner_module, "gateway_run_exec", fake_run_exec)
    engine = runner_module.SupervisedEngineer(
        engineer_runner=object(),
        reviewer=object(),
        engineer_config=runner_module.EngineerConfig(model="m"),
        reviewer_config=None,
    )
    engine._run_engineer(
        prompt="p",
        workdir=Path("."),
        run_label="r",
        supervised_config=SupervisedConfig(**config),
    )
    return captured[0]


def test_a_default_engineer_turn_is_never_stopped_and_its_stall_goes_to_the_manager(monkeypatch) -> None:
    """Thirty minutes without progress asks the Manager to look; it stops nothing."""
    from argus.engineer.turn_stall import LOOK_SECONDS, TurnStallWatch

    options = _capture_engineer_options(monkeypatch, runner_stalled_idle_seconds=1800)
    assert not options.watchdog_hard_idle_seconds
    assert options.watchdog_stalled_idle_seconds == 1800
    assert isinstance(options.inactivity_callback, TurnStallWatch)
    assert options.inactivity_callback.stall_seconds == 1800
    assert options.watchdog_soft_idle_seconds == LOOK_SECONDS
    options = _capture_engineer_options(monkeypatch, runner_stalled_idle_seconds=0)
    assert not options.watchdog_hard_idle_seconds
    assert options.inactivity_callback is None and options.watchdog_stalled_idle_seconds == 0


def _exec_stop(command: str | None) -> str:
    """The stop record the exec transport writes."""
    waiting_on = f"; running tool: {command})" if command else ")."
    return f"Forced restart after hard idle timeout (1800s without a model stream event{waiting_on}"


class _SilentCommandsEngineer:
    """Each turn hangs on the next listed command; None means a healthy turn."""

    def __init__(self, commands: list) -> None:
        self.commands = list(commands)
        self.calls = 0

    def run_exec(self, **_kwargs):
        from argus.core.models import RunnerResult

        command = self.commands[min(self.calls, len(self.commands) - 1)]
        self.calls += 1
        if command is None:
            return RunnerResult(exit_code=0, agent_messages=["done"], thread_id="t")
        return RunnerResult(exit_code=-15, agent_messages=[], fatal_error=_exec_stop(command or None))


class _DoneReviewer:
    def evaluate(self, **_kwargs):
        from argus.core.models import ReviewDecision

        return ReviewDecision(status="done", reason="ok", next_action="")


def _run_loop(tmp_path: Path, engineer: _SilentCommandsEngineer):
    from argus.engineer.runner import EngineerConfig, SupervisedEngineer
    from argus.reviewer import ReviewerConfig

    prompts: list[str] = []
    engine = SupervisedEngineer(
        engineer_runner=engineer,
        reviewer=_DoneReviewer(),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )

    def build(next_action, _static=True):
        prompts.append(str(next_action or ""))
        return "continue " + str(next_action or "")

    status, rounds, _message, reason, _thread = engine.run(
        objective="build the package",
        engineer_prompt_builder=build,
        supervised_config=SupervisedConfig(
            max_rounds=10,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
            checkpoint_path=tmp_path / "CHECKPOINT.md",
        ),
        workdir=tmp_path,
    )
    return status, rounds, reason, prompts


def test_a_different_silent_command_after_the_guidance_does_not_fail_the_round(tmp_path) -> None:
    engineer = _SilentCommandsEngineer(["pip install -e .", "python train.py", None])
    status, _rounds, _reason, prompts = _run_loop(tmp_path, engineer)
    assert status == "done"
    assert engineer.calls == 3
    # The next turn is told which command was stopped and how to run it instead.
    assert "background job" in prompts[1]
    assert "pip install -e ." in prompts[1]
    assert "python train.py" in prompts[2]


def test_the_same_command_hanging_again_fails_the_round(tmp_path) -> None:
    engineer = _SilentCommandsEngineer(["pip install -e ."])
    status, rounds, reason, _prompts = _run_loop(tmp_path, engineer)
    assert status == "error"
    assert engineer.calls == 2
    assert "pip install -e ." in reason


def test_silence_stops_across_different_commands_are_still_bounded(tmp_path) -> None:
    engineer = _SilentCommandsEngineer([f"cmd{i}" for i in range(10)])
    status, _rounds, _reason, _prompts = _run_loop(tmp_path, engineer)
    assert status == "error"
    assert engineer.calls == 4


def test_unnamed_silence_stops_get_generic_guidance_and_are_bounded(tmp_path) -> None:
    # A stream that never names its command cannot show a repeat, so two
    # unnamed stops do not fail the round; the any-command limit still does.
    engineer = _SilentCommandsEngineer(["", "", None])
    status, _rounds, _reason, prompts = _run_loop(tmp_path, engineer)
    assert status == "done"
    assert engineer.calls == 3
    assert "background job" in prompts[1] and "the command it was running" in prompts[1]

    engineer = _SilentCommandsEngineer([""])
    status, _rounds, _reason, _prompts = _run_loop(tmp_path / "again", engineer)
    assert status == "error"
    assert engineer.calls == 4


@pytest.mark.parametrize(
    ("started", "finished"),
    [
        (
            {"type": "item.started", "item": {"type": "command_execution", "command": "pip install -e .", "status": "in_progress"}},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "pip install -e .", "status": "completed"}},
        ),
        (
            {"type": "tool.execution_start", "data": {"toolName": "bash", "arguments": {"command": "pip install -e ."}}},
            {"type": "tool.execution_complete", "data": {"toolCallId": "t1"}},
        ),
        (
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pip install -e ."}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x"}]}},
        ),
        (
            {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "running", "input": {"command": "pip install -e ."}}}},
            {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "completed", "input": {"command": "pip install -e ."}}}},
        ),
    ],
)
def test_exec_stream_tracks_the_command_in_flight(started: dict, finished: dict) -> None:
    current = running_tool_after_event(started, "")
    assert current == "pip install -e ."
    assert running_tool_after_event({"type": "token_count"}, current) == current
    assert running_tool_after_event(finished, current) == ""


def test_exec_transport_hard_idle_stop_names_the_command() -> None:
    runner = AgentCliRunner(agent_bin=sys.executable)
    script = (
        "import json, time\n"
        "print(json.dumps({'type': 'item.started', 'item': {'type': 'command_execution', "
        "'command': 'pip install -e .', 'status': 'in_progress'}}), flush=True)\n"
        "time.sleep(30)\n"
    )
    command = [sys.executable, "-c", script]
    model_call = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=os.name != "nt",
    )
    try:
        state = runner._stream_turn_output(
            process=model_call, command=command,
            options=RunnerOptions(watchdog_hard_idle_seconds=1),
            run_label="test-watchdog", thread_id=None,
        )
        assert state.watchdog_terminated is True
        assert fatal_error_is_idle_termination(state.watchdog_reason)
        assert idle_termination_running_tool(state.watchdog_reason) == "pip install -e ."
    finally:
        if model_call.poll() is None:
            model_call.terminate()
            model_call.wait(timeout=3)
