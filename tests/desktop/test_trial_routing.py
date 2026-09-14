"""Trial regressions through internal routes and the real front-door guard.

Only synthetic state and in-process model doubles; no credentials or network.
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from argus_skill.core import knobs
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.trial import CLIENT_MODEL, client


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
@pytest.mark.parametrize("route", ["classify", "dag", "plan", "rewrite"])
def test_all_automatic_control_routes_use_the_trial_selector(monkeypatch, route, sentinel):
    from argus_skill.manager.dispatch import _bounded_dag_model
    from argus_skill.webapi.manager_bridge import _plan_preview_model, _rewrite_model_and_effort

    routes = {
        "classify": ("ARGUS_SKILL_FRONTDOOR_MODEL", knobs.resolve_manager_classify_model),
        "dag": ("ARGUS_SKILL_BOUNDED_DAG_MODEL", _bounded_dag_model),
        "plan": ("ARGUS_SKILL_PLAN_PREVIEW_MODEL", _plan_preview_model),
        "rewrite": ("ARGUS_SKILL_REWRITE_MODEL", lambda: _rewrite_model_and_effort()[0]),
    }
    name, resolve = routes[route]
    monkeypatch.setenv(name, sentinel)
    assert resolve() == CLIENT_MODEL


def test_trial_catalog_is_not_a_personal_openai_catalog():
    assert not knobs.backend_uses_openai_catalog("copilot")


def test_trial_role_auto_and_unconfigured_model_resolve_identically(monkeypatch):
    assert knobs.resolve_role_model("manager", backend="copilot") == CLIENT_MODEL
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "auto")
    assert knobs.resolve_role_model(
        "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend="copilot",
    ) == CLIENT_MODEL


def test_explicit_incompatible_control_model_remains_visible_and_is_rejected(monkeypatch):
    from argus_skill.adapters.agent_cli_backend import _exec

    monkeypatch.setenv("ARGUS_SKILL_FRONTDOOR_MODEL", "own-explicit-model")
    model = knobs.resolve_manager_classify_model()
    assert model == "own-explicit-model"
    backend = SimpleNamespace(
        _runner=SimpleNamespace(backend="copilot"),
        _refresh_known_secret_values=lambda: None,
        _resolve_execution_options=lambda value: value,
    )
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **k: pytest.fail("must not spawn"))
    result = _exec.execute(backend, prompt="test", options=RunnerOptions(model=model), run_label="test")
    assert result.exit_code != 0
    assert "服务端选择真实模型" in result.fatal_error
    assert os.environ["ARGUS_SKILL_FRONTDOOR_MODEL"] == "own-explicit-model"


def test_personal_mode_preserves_cheap_model_routing(monkeypatch):
    monkeypatch.setenv(client.TRIAL_ENV, "0")
    assert knobs.resolve_manager_classify_model(backend="copilot") == "gpt-5.4-mini"


def test_trial_flag_does_not_replace_another_backends_model(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "other-provider-model")
    assert knobs.resolve_manager_classify_model(backend="pi") == "other-provider-model"


def test_persisted_personal_control_knob_is_not_rewritten(isolated_trial, monkeypatch):
    from argus_skill.core.knob_store import read_persisted_knobs, write_persisted_knobs

    assert write_persisted_knobs({"ARGUS_SKILL_FRONTDOOR_MODEL": "own-saved-model"})
    before = read_persisted_knobs()
    monkeypatch.setenv("ARGUS_SKILL_MODEL", CLIENT_MODEL)
    assert knobs.resolve_manager_classify_model() == CLIENT_MODEL
    assert read_persisted_knobs() == before


def test_front_door_classification_reaches_execution_in_trial_mode(isolated_trial, monkeypatch):
    from argus_skill.adapters.agent_cli_backend import _exec
    from argus_skill.life import router
    from argus_skill.manager import _front_door_ops

    calls = []
    backend = SimpleNamespace(
        backend="copilot", _runner=SimpleNamespace(backend="copilot"),
        _refresh_known_secret_values=lambda: None,
        _resolve_execution_options=lambda value: value,
    )
    def execute_prepared(_backend, **kwargs):
        calls.append(kwargs["options"].model)
        return RunnerResult(exit_code=0, agent_messages=["synthetic classification"])
    monkeypatch.setattr(_exec, "_execute_prepared", execute_prepared)
    monkeypatch.setattr(_front_door_ops, "gateway_run_exec", _exec.execute)
    monkeypatch.setattr(router, "classify_front_door", lambda text, *, run_exec, **kw: run_exec(text))
    manager = SimpleNamespace(
        runner=backend, manager_session_root=isolated_trial / "manager",
        _task_usage_scope=lambda _: nullcontext(),
    )
    result = _front_door_ops._FrontDoorMixin.classify_front_door(manager, "A normal test message")
    assert result.exit_code == 0, result.fatal_error
    assert calls == [CLIENT_MODEL]


@pytest.mark.parametrize("model", [None, "", "auto", "AUTO", "default", CLIENT_MODEL])
def test_one_shot_and_acp_share_the_same_trial_selector(model):
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
    from argus_skill.agent_cli.agent_cli_runner import RunnerOptions as CliOptions
    from argus_skill.agent_cli.copilot_acp import CopilotAcpClient

    options = CliOptions(model=model, reasoning_effort="low")
    command = AgentCliRunner("copilot", backend="copilot")._build_command(
        resume_thread_id=None, options=options,
    )
    assert command[command.index("--model") + 1] == CLIENT_MODEL
    acp = CopilotAcpClient("copilot", model=model, reasoning_effort="low")
    assert acp._model == CLIENT_MODEL and acp._reasoning_effort == "low"


def test_isolated_trial_retains_only_its_selected_provider(isolated_trial, monkeypatch):
    import json

    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
    from argus_skill.agent_cli.agent_cli_runner import RunnerOptions as CliOptions
    from argus_skill.trial.storage import write_private

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

    from argus_skill.trial import attention
    from argus_skill.trial.storage import write_private

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

    from argus_skill.adapters.agent_cli_backend import _exec
    from argus_skill.core import workbench_plugins

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
