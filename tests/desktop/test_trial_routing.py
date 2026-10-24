"""Trial regressions through internal routes and the real front-door guard.

Only synthetic state and in-process model doubles; no credentials or network.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from argus.core import knobs
from argus.core.models import RunnerOptions
from argus.trial import CLIENT_MODEL, client


@pytest.fixture(autouse=True)
def isolated_trial(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("ARGUS_SKILL_") or name in {
            "ARGUS_WORKBENCH_HOST_ROOT", "ARGUS_DESKTOP_TRIAL_PROFILE",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv(client.TRIAL_ENV, "1")
    return tmp_path


@pytest.mark.parametrize("sentinel", ["", "auto", "inherit", "default"])
def test_automatic_light_model_uses_the_trial_selector(monkeypatch, sentinel):
    monkeypatch.setenv("ARGUS_SKILL_LIGHT_MODEL", sentinel)
    assert knobs.resolve_light_model() == CLIENT_MODEL


@pytest.mark.parametrize("model", [None, "", "auto", "AUTO", "default", CLIENT_MODEL])
def test_copilot_calls_use_the_trial_selector(model):
    from argus.agent_cli.agent_cli_runner import AgentCliRunner
    from argus.agent_cli.agent_cli_runner import RunnerOptions as CliOptions

    options = CliOptions(model=model, reasoning_effort="low")
    command = AgentCliRunner("copilot", backend="copilot")._build_command(
        resume_thread_id=None, options=options,
    )
    assert command[command.index("--model") + 1] == CLIENT_MODEL
    assert command[command.index("--reasoning-effort") + 1] == "low"


def test_isolated_trial_retains_only_its_selected_provider(isolated_trial, monkeypatch):
    import json

    from argus.agent_cli.agent_cli_runner import AgentCliRunner
    from argus.agent_cli.agent_cli_runner import RunnerOptions as CliOptions
    from argus.trial.storage import write_private

    fake_key = "argus_trial_" + "e" * 64
    write_private(isolated_trial / "copilot-trial.json", json.dumps({
        "base_url": "https://argusbot.cn/v1", "api_key": fake_key,
    }).encode())
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-unrelated")
    monkeypatch.setenv("GH_TOKEN", "dummy-unrelated")
    child = AgentCliRunner("copilot", backend="copilot")._child_env(
        CliOptions(isolate_workdir=True),
    )
    assert child["COPILOT_PROVIDER_API_KEY"] == fake_key
    assert child["COPILOT_PROVIDER_WIRE_MODEL"] == CLIENT_MODEL
    assert "OPENAI_API_KEY" not in child and "GH_TOKEN" not in child


def test_trial_profile_and_pause_remain_host_scoped_in_plugin_daemon(isolated_trial, monkeypatch):
    import json

    from argus.trial import attention
    from argus.trial.storage import write_private

    write_private(isolated_trial / "copilot-trial.json", json.dumps({
        "base_url": "https://argusbot.cn/v1", "api_key": "argus_trial_" + "f" * 64,
    }).encode())
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(isolated_trial))
    workbench = isolated_trial / "plugins" / "crystalpilot" / "workbench"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(workbench))
    assert client.profile_path() == isolated_trial / "copilot-trial.json"
    attention.record_failure("provider_stream_incomplete")
    assert attention.reason()
    assert (isolated_trial / "trial-attention.json").is_file()
    assert not (workbench / "copilot-trial.json").exists()
    assert not (workbench / "trial-attention.json").exists()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(isolated_trial))
    assert attention.reason()  # The parent sees the same no-replay decision.


def test_plugin_model_override_is_validated_before_accounting_and_finished(monkeypatch):
    from dataclasses import replace

    from argus.adapters.agent_cli_backend import _exec
    from argus.core import workbench_plugins

    finished = []
    monkeypatch.setattr(workbench_plugins, "prepare_plugin_run", lambda prompt, options, **kw: (
        prompt, replace(options, model="incompatible-plugin-model"),
    ))
    monkeypatch.setattr(workbench_plugins, "finish_plugin_run", finished.append)
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **k: pytest.fail("No reservation or model call"))
    backend = SimpleNamespace(_runner=SimpleNamespace(backend="copilot"),
                              _refresh_known_secret_values=lambda: None,
                              _resolve_execution_options=lambda value: value)
    result = _exec.execute(backend, prompt="fixture", options=RunnerOptions(model=CLIENT_MODEL), run_label="engineer-r1")
    assert result.exit_code == 1 and result.stop_kind == "permanent_error"
    assert len(finished) == 1  # An already-prepared binding must be released.
