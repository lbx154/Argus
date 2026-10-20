"""Planner and Observer requests carry only the CLI tools they use.

Neither role starts agents or workflows. But the global yolo policy lifts the
Planner's read-only mode and the Observer runs with the Engineer's access, so
every request carried Copilot CLI's whole surface: 26 tools including task,
sub-agents, dynamic workflows and session SQL, 10K schema tokens of a ~40K
Planner turn. The Planner keeps GitHub/web search and apply_patch (its contract
sends it to primary sources; wiki upkeep edits pages); the Observer only reads
the event log.
"""
from __future__ import annotations

from pathlib import Path

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.models import (
    READING_ROLE_UNUSED_COPILOT_TOOLS,
    RunnerOptions,
    RunnerResult,
)
from argus.life.observer import look
from argus.planner.planner import Planner, PlannerConfig

_READING = {"bash", "view", "rg", "glob", "skill"}


class _Recorder:
    backend = "copilot"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.options: list[RunnerOptions] = []

    def fork(self, event_callback=None):  # noqa: ANN001
        return self

    def run_exec(self, *, prompt, options, run_label=None, resume_thread_id=None):  # noqa: ANN001
        self.options.append(options)
        return RunnerResult(exit_code=0, agent_messages=[self.reply], thread_id="t1")


def _copilot_command(options: RunnerOptions, monkeypatch, *, safe_mode: bool) -> list[str]:
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1" if safe_mode else "0")
    backend = AgentCliBackend(backend="copilot")
    cli = backend._runner._apply_sandbox_policy(backend._translate_options(options))
    return backend._runner._build_copilot_command(resume_thread_id=None, options=cli)


def _excluded(command: list[str]) -> set[str]:
    arg = next(arg for arg in command if arg.startswith("--excluded-tools="))
    return set(arg.split("=", 1)[1].split(","))


def _planner_options(tmp_path: Path) -> RunnerOptions:
    runner = _Recorder("PROJECT_DONE=false\nREASON=wait for the running check")
    Planner(runner).plan_next(
        continuous_objective="improve the solver",
        journal_tail="",
        config=PlannerConfig(working_dir=str(tmp_path), state_root=str(tmp_path)),
    )
    return runner.options[0]


def test_planner_request_drops_the_tools_it_never_uses(tmp_path, monkeypatch) -> None:
    command = _copilot_command(_planner_options(tmp_path), monkeypatch, safe_mode=False)

    excluded = _excluded(command)
    assert set(READING_ROLE_UNUSED_COPILOT_TOOLS) <= excluded
    assert {"sql", "fetch_copilot_cli_documentation"} <= excluded
    assert not excluded & (_READING | {"apply_patch", "web_fetch"})
    assert "--disable-mcp-server" not in command
    assert "--yolo" in command


def test_planner_keeps_its_read_only_allowlist_in_safe_mode(tmp_path, monkeypatch) -> None:
    command = _copilot_command(_planner_options(tmp_path), monkeypatch, safe_mode=True)

    assert "--available-tools" in command
    assert not any(arg.startswith("--excluded-tools") for arg in command)
    assert "--disable-mcp-server" not in command


def test_observer_look_drops_the_tools_it_never_uses(tmp_path, monkeypatch) -> None:
    backend = _Recorder("The flow is fine.")
    look(backend, root=tmp_path, turns=[{"role": "engineer", "item_id": "", "ts": 1.0}], model="m")

    command = _copilot_command(backend.options[0], monkeypatch, safe_mode=False)
    excluded = _excluded(command)
    assert set(READING_ROLE_UNUSED_COPILOT_TOOLS) | {"apply_patch"} <= excluded
    assert not excluded & _READING
    index = command.index("--disable-mcp-server")
    assert command[index + 1] == "github-mcp-server"
