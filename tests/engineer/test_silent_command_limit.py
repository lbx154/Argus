"""A command that prints nothing for the idle limit is stopped, named, and not repeated as it was."""
from __future__ import annotations

import json
from pathlib import Path

from argus.agent_cli import copilot_acp
from argus.agent_cli.copilot_acp import CopilotAcpClient
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_stop_signals import (
    fatal_error_is_idle_termination,
    idle_termination_review_decision,
    idle_termination_running_tool,
)

REASON = (
    "Forced restart after hard idle timeout (1800s without an ACP stream event; "
    "last event: tool_call; running tool: find / -name '*pdftoppm*')"
)


def test_engineer_turns_have_a_default_silence_limit(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", raising=False)
    assert SupervisedConfig().runner_hard_idle_seconds == 1800
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "0")
    assert SupervisedConfig().runner_hard_idle_seconds == 0


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


def test_acp_turn_tracks_the_tool_in_flight() -> None:
    client = CopilotAcpClient("copilot-bin")
    emitted: list[str] = []
    turn = copilot_acp._Turn("s1", None, emitted.append, allow_persistent=True)
    client._active_turn = turn

    def update(payload: dict) -> None:
        client._handle_notification("session/update", {"sessionId": "s1", "update": payload})

    assert turn.running_tool == ""
    update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "find / -name '*pdftoppm*'",
            "kind": "execute", "status": "pending", "rawInput": {"command": "find / -name '*pdftoppm*'"}})
    assert turn.running_tool == "find / -name '*pdftoppm*'"
    update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"})
    assert turn.running_tool == "find / -name '*pdftoppm*'"
    update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
    assert turn.running_tool == ""
    structured = [json.loads(line) for line in emitted if line.startswith("{")]
    assert [event["type"] for event in structured] == ["tool.call", "tool.result"]


def test_running_tool_keeps_commands_that_end_in_a_parenthesis() -> None:
    reason = (
        "Forced restart after hard idle timeout (1800s without an ACP stream event; "
        "last event: tool_call; running tool: echo $(date))"
    )
    assert idle_termination_running_tool(reason) == "echo $(date)"


def test_finishing_one_call_does_not_hide_another_with_the_same_title() -> None:
    client = CopilotAcpClient("copilot-bin")
    turn = copilot_acp._Turn("s1", None, lambda _line: None, allow_persistent=True)
    client._active_turn = turn

    def update(payload: dict) -> None:
        client._handle_notification("session/update", {"sessionId": "s1", "update": payload})

    update({"sessionUpdate": "tool_call", "toolCallId": "a", "title": "bash", "status": "pending"})
    update({"sessionUpdate": "tool_call", "toolCallId": "b", "title": "bash", "status": "pending"})
    update({"sessionUpdate": "tool_call_update", "toolCallId": "b", "status": "completed"})
    assert turn.running_tool == "bash"
    update({"sessionUpdate": "tool_call_update", "toolCallId": "a", "status": "completed"})
    assert turn.running_tool == ""


def _capture_engineer_options(monkeypatch, hard_idle: int):
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
        supervised_config=SupervisedConfig(runner_hard_idle_seconds=hard_idle),
    )
    return captured[0]


def test_turning_the_hard_stop_off_keeps_the_stalled_alert(monkeypatch) -> None:
    options = _capture_engineer_options(monkeypatch, 0)
    assert options.watchdog_hard_idle_seconds == 0
    # None leaves the backend's own likely-stalled alert in place; 0 would turn it off.
    assert options.watchdog_stalled_idle_seconds is None
    options = _capture_engineer_options(monkeypatch, 1800)
    assert options.watchdog_hard_idle_seconds == 1800
    assert 0 < options.watchdog_stalled_idle_seconds < 1800


class _SilentCommandsEngineer:
    """Each turn hangs on the next listed command; None means a healthy turn."""

    def __init__(self, commands: list[str | None]) -> None:
        self.commands = list(commands)
        self.calls = 0

    def run_exec(self, **_kwargs):
        from argus.core.models import RunnerResult

        command = self.commands[min(self.calls, len(self.commands) - 1)]
        self.calls += 1
        if command is None:
            return RunnerResult(exit_code=0, agent_messages=["done"], thread_id="t")
        return RunnerResult(
            exit_code=-15,
            agent_messages=[],
            fatal_error=(
                "Forced restart after hard idle timeout (1800s without an ACP stream "
                f"event; last event: tool_call; running tool: {command})"
            ),
        )


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
            runner_hard_idle_seconds=0,
            background_subagent_advisory=False,
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
