"""Plugin installation roots survive real task process boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.core import plugin_manager as pm


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)
    monkeypatch.setattr(pm, "_loaded", {})
    return root


def test_missing_plugin_has_a_terminal_event_and_no_mission_execution(host):
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor._mission_execution import MissionExecutionMixin

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


def test_task_preflight_reports_missing_plugin_before_spawning(host, monkeypatch):
    from argus.core.pipeline_state import write_pipeline_state
    from argus.daemon import _life_worker_admission as admission
    from argus.daemon.config import LifeWorkerConfig

    life = host / "projects" / "s-fixture"
    life.mkdir(parents=True)
    write_pipeline_state(life, {"vertical": "crystalpilot"})
    config = LifeWorkerConfig(life_dir=life, global_root=host, project_workdir=life)
    monkeypatch.setattr(admission.subprocess, "run", lambda *a, **k: pytest.fail("No child may spawn"))
    assert admission.spawn_detached_daemon_clean(config, quiet=True) != 0
    assert "crystalpilot" in config.last_spawn_error


def test_old_workbench_research_route_cannot_silently_resume(host, monkeypatch):
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor.backlog_guard import ensure_manager_decision

    memory = LifeMemory.open(host / "projects" / "s-crystalpilot-01234567")
    item = memory.backlog.add(BacklogItem.new(title="old failed attempt", objective="preserve it",
        manager_decision={"routed": True, "vertical": "research"}))
    monkeypatch.setattr(pm, "require_plugin", lambda name: SimpleNamespace(owns_workdir=lambda _: True))
    with pytest.raises(pm.PluginUnavailableError, match="新建会话"):
        ensure_manager_decision(memory, item)
    assert memory.backlog.all()[0].manager_decision["vertical"] == "research"


def test_missing_workbench_binding_fails_before_tool_or_model_preparation(host, monkeypatch):
    from argus.core.models import RunnerOptions
    from argus.core.workbench_plugins import prepare_plugin_run

    monkeypatch.setattr(pm, "require_plugin", lambda name: SimpleNamespace(owns_workdir=lambda _: False))
    with pytest.raises(pm.PluginUnavailableError, match="绑定缺失"):
        prepare_plugin_run("fixture", RunnerOptions(working_dir=str(host)), backend="pi",
                           run_label="engineer-r1", project_root=host / "s-crystalpilot-01234567")
