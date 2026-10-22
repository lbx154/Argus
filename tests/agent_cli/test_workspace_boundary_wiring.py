"""SE-1: Engineer and Reviewer calls carry a workspace boundary to their CLI's hook.

Before this, both roles ran with the agent CLI's full access and nothing at
the tool layer stopped them reading another project's workspace or state.
These tests pin that each role's call now carries a boundary that closes the
other project, and that each supported backend's command attaches the
pre-tool hook enforcing it (the hook itself is exercised in
``tests/core/test_workspace_isolation.py`` and, against the real CLIs, in
``test_workspace_boundary_cli.py``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli import _workspace_boundary
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.core.workspace_boundary import WorkspaceBoundary
from argus.engineer.round_config import SupervisedConfig


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    home = tmp_path / "home"
    projects = {}
    for name in ("mine", "theirs"):
        workdir = tmp_path / "work" / name
        workdir.mkdir(parents=True)
        (workdir / "secret.txt").write_text(name)
        state = home / "projects" / name
        state.mkdir(parents=True)
        (state / "session.json").write_text(json.dumps({"id": name, "workdir": str(workdir)}))
        projects[name] = (workdir, state)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.delenv("ARGUS_SKILL_FOREIGN_ROOTS", raising=False)
    return projects


def _closes_theirs_not_mine(boundary, projects) -> None:
    assert isinstance(boundary, WorkspaceBoundary)
    (mine, mine_state), (theirs, theirs_state) = projects["mine"], projects["theirs"]
    for path in (theirs / "secret.txt", theirs_state / "session.json"):
        assert boundary.refusal(str(path), str(mine)), path
    for path in (mine / "secret.txt", mine_state / "events.jsonl"):
        assert not boundary.refusal(str(path), str(mine)), path


def test_an_engineer_turn_carries_a_boundary_closing_other_projects(two_projects, monkeypatch):
    from argus.core.models import RunnerResult
    from argus.engineer import runner as runner_module

    captured = []
    monkeypatch.setattr(runner_module, "gateway_run_exec",
                        lambda _runner, *, prompt, options, **_: captured.append(options)
                        or RunnerResult(exit_code=0, agent_messages=["ok"]))
    mine, state = two_projects["mine"]
    engine = runner_module.SupervisedEngineer(
        engineer_runner=object(), reviewer=object(),
        engineer_config=runner_module.EngineerConfig(model="m"), reviewer_config=None,
    )
    engine._run_engineer(prompt="p", workdir=mine, run_label="engineer-r1",
                         supervised_config=SupervisedConfig(
                             state_root=state,
                             engineer_log_path=str(state / "events.jsonl"),
                         ))

    _closes_theirs_not_mine(captured[0].workspace_boundary, two_projects)


def test_a_reviewer_call_carries_a_boundary_closing_other_projects(two_projects):
    from argus.reviewer import Reviewer, ReviewerConfig

    mine, state = two_projects["mine"]
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(review_action=("submit_review", {"review": "Fine.\n→ engineer"})))
    Reviewer(backend).evaluate(
        objective="Review", round_index=1, session_id=None, main_summary="done", main_error=None,
        engineer_log_path=str(state / "events.jsonl"),
        config=ReviewerConfig(working_dir=str(mine), vertical_state_root=str(state)),
    )

    _closes_theirs_not_mine(backend.history[0][2].workspace_boundary, two_projects)


def test_the_cli_adapter_passes_the_boundary_through():
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.core.models import RunnerOptions as CoreOptions

    boundary = WorkspaceBoundary(own=("/w",), closed=("/x",))
    backend = AgentCliBackend(backend="codex")
    assert backend._translate_options(CoreOptions(workspace_boundary=boundary)).workspace_boundary is boundary


def _prepared_command(backend: str, options: RunnerOptions):
    runner = AgentCliRunner(agent_bin=backend, backend=backend)
    with _workspace_boundary.workspace_boundary_call(backend, options) as prepared:
        return prepared, runner._build_command(resume_thread_id=None, options=prepared), runner._child_env(prepared)


BOUNDARY = WorkspaceBoundary(own=("/work/mine",), closed=("/work/theirs",), workdir="/work/mine")


def test_claude_gets_an_inline_pre_tool_hook():
    _, argv, _ = _prepared_command("claude", RunnerOptions(dangerous_yolo=True, workspace_boundary=BOUNDARY))
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["disableAllHooks"] is False
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "*"
    assert "workspace_boundary.py" in hook["hooks"][0]["command"]
    assert "--format claude" in hook["hooks"][0]["command"]


def test_codex_gets_a_trusted_inline_pre_tool_hook():
    _, argv, _ = _prepared_command("codex", RunnerOptions(dangerous_yolo=True, workspace_boundary=BOUNDARY))
    assert "--dangerously-bypass-hook-trust" in argv
    hooks = next(value for value in argv if value.startswith("hooks.PreToolUse="))
    assert "workspace_boundary.py" in hooks and "--format codex" in hooks
    assert "features.hooks=true" in argv


def test_copilot_gets_a_plugin_whose_hook_denies(tmp_path):
    with _workspace_boundary.workspace_boundary_call(
        "copilot", RunnerOptions(dangerous_yolo=True, workspace_boundary=BOUNDARY),
    ) as prepared:
        argv = AgentCliRunner(agent_bin="copilot", backend="copilot")._build_command(
            resume_thread_id=None, options=prepared)
        plugin = Path(argv[argv.index("--plugin-dir") + 1])
        hooks = json.loads((plugin / "hooks" / "hooks.json").read_text())
        boundary_file = Path(prepared._boundary_hook[prepared._boundary_hook.index("--boundary") + 1])
        assert WorkspaceBoundary.from_json(boundary_file.read_text()) == BOUNDARY
    command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--format copilot" in command and "--sha256" in command
    assert not plugin.exists()  # the call's hook files go with the call


def test_pi_gets_the_boundary_extension_and_its_hook():
    prepared, argv, env = _prepared_command("pi", RunnerOptions(dangerous_yolo=True, workspace_boundary=BOUNDARY))
    assert str(_workspace_boundary.PI_EXTENSION) in argv
    assert json.loads(env[_workspace_boundary.PI_HOOK_ENV]) == prepared._boundary_hook


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "pi"])
def test_without_a_boundary_or_tools_the_command_is_unchanged(backend):
    runner = AgentCliRunner(agent_bin=backend, backend=backend)
    plain = RunnerOptions(dangerous_yolo=True)
    expected = runner._build_command(resume_thread_id=None, options=plain)
    for options in (plain, RunnerOptions(dangerous_yolo=True, disable_tools=True, workspace_boundary=BOUNDARY)):
        with _workspace_boundary.workspace_boundary_call(backend, options) as prepared:
            argv = runner._build_command(resume_thread_id=None, options=prepared)
        if not options.disable_tools:
            assert argv == expected
        assert not any("workspace_boundary" in part for part in argv)
        assert "--dangerously-bypass-hook-trust" not in argv


def test_a_backend_without_a_hook_says_so_once(caplog, monkeypatch):
    monkeypatch.setattr(_workspace_boundary, "_UNSUPPORTED_WARNED", set())
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with _workspace_boundary.workspace_boundary_call(
                "cursor", RunnerOptions(workspace_boundary=BOUNDARY),
            ) as prepared:
                assert prepared._boundary_hook is None
    warnings = [r for r in caplog.records if "without the workspace boundary" in r.getMessage()]
    assert len(warnings) == 1


def test_an_enrolled_trial_workspace_closes_every_other_tenant(tmp_path):
    from types import SimpleNamespace

    from argus.trial.admin_runtime import other_tenant_roots

    tenants = {
        key: SimpleNamespace(uds=str(tmp_path / "tenants" / key / "run" / "web.sock"))
        for key in ("trial-01", "trial-02", "trial-11")
    }
    settings = SimpleNamespace(tenants=tenants, state_dir=tmp_path / "portal", key_file=tmp_path / "master.key")

    roots = other_tenant_roots(settings, "trial-11")

    assert roots == [
        str(tmp_path / "tenants" / "trial-01"), str(tmp_path / "tenants" / "trial-02"),
        str(tmp_path / "portal"), str(tmp_path / "master.key"),
    ]
