"""Plugin installation roots survive real task process boundaries."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from argus_skill.core import plugin_manager as pm


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)
    monkeypatch.setattr(pm, "_loaded", {})
    return root


@pytest.mark.parametrize("frozen", [False, True])
def test_product_clean_spawn_pins_host_before_child_switches_task_home(host, monkeypatch, frozen):
    from argus_skill.daemon import _life_worker_admission as admission
    from argus_skill.daemon.config import LifeWorkerConfig

    task_root = host / "plugins" / "crystalpilot" / "workbench"
    life = task_root / "projects" / "s-fixture"
    workdir = host / "test-workspace"
    life.mkdir(parents=True)
    workdir.mkdir()
    pm.write_json(host / "extensions" / "registry.json", {"fixture": {"enabled": False}})
    config = LifeWorkerConfig(life_dir=life, global_root=task_root, project_workdir=workdir)
    captured = {}
    actual_run = subprocess.run
    def fake_run(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(admission.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(admission.subprocess, "run", fake_run)
    monkeypatch.setattr(admission, "spawn_detached_daemon", lambda *a, **k: pytest.fail(
        "Packaged execution must retain the clean helper boundary"
    ))
    assert admission.spawn_detached_daemon_clean(config, quiet=True) == 0
    assert captured["env"]["ARGUS_WORKBENCH_HOST_ROOT"] == str(host.resolve())
    # The test does NOT inject the missing host variable. Run a second process
    # using the environment produced above by the actual product spawn path.
    code = f'''
import json, os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from argus_skill.daemon._life_worker_boot import LifeWorkerBootMixin
from argus_skill.core import plugin_manager
worker = SimpleNamespace(
    config=SimpleNamespace(global_root=Path({str(task_root)!r}), project_workdir=None),
    _install_signal_handlers=lambda: None,
    _rf_export_configured_backend=lambda: None,
)
with patch("argus_skill.daemon._life_worker_boot.configure_framework_python_env"), patch("argus_skill.tools.capability_vault.gpu_env_vars", return_value={{}}):
    LifeWorkerBootMixin._rf_bootstrap_environment(worker)
print(json.dumps({{"host": str(plugin_manager.host_root()), "task": os.environ["ARGUS_SKILL_HOME"], "registry": plugin_manager.registry()}}))
'''
    completed = actual_run(
        [sys.executable, "-c", code], cwd=captured["cwd"], env=captured["env"],
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=True,
    )
    result = json.loads(completed.stdout)
    assert result["host"] == str(host.resolve())
    assert result["task"] == str(task_root.resolve())
    assert result["registry"] == {"fixture": {"enabled": False}}
    assert not (task_root / "extensions").exists()


def test_explicit_missing_plugin_is_not_a_research_fallback(host, tmp_path):
    from argus_skill.core.pipeline_state import write_pipeline_state
    from argus_skill.skills.vertical_select import resolve_vertical

    state = tmp_path / "session"
    write_pipeline_state(state, {"vertical": "crystalpilot"})
    with pytest.raises(pm.PluginError, match="crystalpilot"):
        resolve_vertical(state)


def test_missing_plugin_backlog_is_not_reclassified_or_overwritten(host, monkeypatch):
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor.backlog_guard import ensure_manager_decision
    from argus_skill.manager import front_door

    memory = LifeMemory.open(host / "session")
    decision = {"routed": True, "vertical": "crystalpilot"}
    item = memory.backlog.add(BacklogItem.new(title="plugin task", objective="synthetic task", manager_decision=decision))
    monkeypatch.setattr(front_door, "prepare_manager_execution_task", lambda *a, **k: pytest.fail(
        "An explicitly selected plugin must never become a generic research task"
    ))
    with pytest.raises(pm.PluginError, match="crystalpilot"):
        ensure_manager_decision(memory, item)
    assert memory.backlog.all()[0].manager_decision == decision


def test_missing_plugin_has_a_terminal_event_and_no_mission_execution(host):
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor._mission_execution import MissionExecutionMixin

    memory = LifeMemory.open(host / "session")
    item = memory.backlog.add(BacklogItem.new(
        title="plugin task", objective="synthetic task",
        manager_decision={"routed": True, "vertical": "crystalpilot"},
    ))
    events = []
    supervisor = SimpleNamespace(
        memory=memory, config=SimpleNamespace(), manager=None,
        _resolve_mission_workdir=lambda _: host,
        _mission_vertical_root=lambda *a: memory.root,
        _build_mission_prelude=lambda _: pytest.fail("No model/mission may run"),
        _emit=events.append,
    )
    result = MissionExecutionMixin._run_one(supervisor, item)
    assert result["status"] == "failed"
    assert result["outcome_class"] == "blocked"
    assert memory.backlog.all()[0].status == "failed"
    assert memory.backlog.all()[0].manager_decision["vertical"] == "crystalpilot"
    assert events[-1]["type"] == "life.mission.completed"
    assert events[-1]["success"] is False
    assert events[-1]["stop_kind"] == "permanent_error"


def test_builtin_and_undecided_library_sessions_keep_existing_behavior(host):
    from argus_skill.core.pipeline_state import write_pipeline_state
    from argus_skill.skills.vertical_select import resolve_vertical

    write_pipeline_state(host / "builtin", {"vertical": "software"})
    assert resolve_vertical(host / "builtin") == "software"
    assert resolve_vertical(host / "undecided") == "research"


def test_task_preflight_reports_missing_plugin_before_spawning(host, monkeypatch):
    from argus_skill.core.pipeline_state import write_pipeline_state
    from argus_skill.daemon import _life_worker_admission as admission
    from argus_skill.daemon.config import LifeWorkerConfig

    life = host / "projects" / "s-fixture"
    life.mkdir(parents=True)
    write_pipeline_state(life, {"vertical": "crystalpilot"})
    config = LifeWorkerConfig(life_dir=life, global_root=host, project_workdir=life)
    monkeypatch.setattr(admission.subprocess, "run", lambda *a, **k: pytest.fail("No child may spawn"))
    assert admission.spawn_detached_daemon_clean(config, quiet=True) != 0
    assert "crystalpilot" in config.last_spawn_error


def test_old_workbench_research_route_cannot_silently_resume(host, monkeypatch):
    from argus_skill.life.memory import BacklogItem, LifeMemory
    from argus_skill.life.supervisor.backlog_guard import ensure_manager_decision

    memory = LifeMemory.open(host / "projects" / "s-crystalpilot-01234567")
    item = memory.backlog.add(BacklogItem.new(title="old failed attempt", objective="preserve it",
        manager_decision={"routed": True, "vertical": "research"}))
    monkeypatch.setattr(pm, "require_plugin", lambda name: SimpleNamespace(owns_workdir=lambda _: True))
    with pytest.raises(pm.PluginUnavailableError, match="新建会话"):
        ensure_manager_decision(memory, item)
    assert memory.backlog.all()[0].manager_decision["vertical"] == "research"


def test_missing_workbench_binding_fails_before_tool_or_model_preparation(host, monkeypatch):
    from argus_skill.core.models import RunnerOptions
    from argus_skill.core.workbench_plugins import prepare_plugin_run

    monkeypatch.setattr(pm, "require_plugin", lambda name: SimpleNamespace(owns_workdir=lambda _: False))
    with pytest.raises(pm.PluginUnavailableError, match="绑定缺失"):
        prepare_plugin_run("fixture", RunnerOptions(working_dir=str(host)), backend="pi",
                           run_label="engineer-r1", project_root=host / "s-crystalpilot-01234567")


def test_trial_profile_location_remains_host_scoped_without_copying_credentials(host, monkeypatch):
    from argus_skill.trial.client import profile_path

    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(host))
    workbench = host / "plugins" / "crystalpilot" / "workbench"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(workbench))
    assert profile_path() == host / "copilot-trial.json"
    assert not (workbench / "copilot-trial.json").exists()


def test_native_session_can_keep_its_original_vertical_with_optional_tools(host, monkeypatch):
    monkeypatch.setattr(pm, "require_plugin", lambda *a: pytest.fail("Native namespace is not pinned to a plugin"))
    assert pm.require_session_plugin(host / "s-01234567", vertical="research") is None
