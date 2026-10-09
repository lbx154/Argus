"""A role process must not steer its own project's daemon through Argus's API.

In a bounded run an Engineer, told to keep its objective out of automatic
rounds, imported Argus's own state API from the interpreter it was given and
set ``continuous.json`` to ``enabled=false``. The daemon read that as a
finished campaign and exited. These tests pin the refusal and its scope: a role
cannot change the control state of the project it works for, but can run Argus
against any other project (a scratch project, a nested Argus, a benchmark
subagent), and an orchestrator started inside a role manages its own root.

This closes the observed path (a role calling Argus's API). A role that edits
its own environment or the raw JSON is not stopped here; that needs OS-level
isolation of role processes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus.core import control_state_guard as guard
from argus.core.control_state_guard import (
    ROLE_PROCESS_ENV,
    ROLE_PROTECTED_ROOTS_ENV,
    RoleControlStateWriteDenied,
    mark_role_process_env,
    orchestrate_state_root,
    protected_roots,
    release_role_marker_for,
)
from argus.core.runtime_incidents import RuntimeIncidentStore
from argus.daemon import state as daemon_state
from argus.daemon.commands import submit_daemon_command
from argus.daemon.state import (
    compare_and_swap_continuous_config,
    disable_continuous_config,
    read_continuous_state,
    request_daemon_control_stop,
    request_daemon_drain,
    write_continuous_config,
)

OBJECTIVE = "Build the dispatch CLI described in the packet."


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(guard, "_ORCHESTRATED", {})
    monkeypatch.delenv(ROLE_PROCESS_ENV, raising=False)
    monkeypatch.delenv(ROLE_PROTECTED_ROOTS_ENV, raising=False)


def _armed(root: Path) -> Path:
    write_continuous_config(root, enabled=True, objective=OBJECTIVE, open_ended=False)
    return root


def _as_role_of(monkeypatch, *roots: Path, role: str = "engineer") -> None:
    """Enter the environment Argus gives a role working for ``roots``."""
    orchestrate_state_root(*roots)
    for key, value in mark_role_process_env(dict(os.environ), role).items():
        monkeypatch.setenv(key, value)


def test_role_process_cannot_disable_its_own_campaign_through_argus_api(tmp_path, monkeypatch):
    life_dir = _armed(tmp_path / "project")
    current = read_continuous_state(life_dir)
    _as_role_of(monkeypatch, life_dir)

    with pytest.raises(RoleControlStateWriteDenied) as denied:
        compare_and_swap_continuous_config(
            life_dir, expected=current, enabled=False,
            objective=current.objective, open_ended=current.open_ended,
        )
    message = str(denied.value)
    assert "operator or the Manager" in message
    # The refusal does not teach the role which variable to drop.
    assert ROLE_PROCESS_ENV not in message and ROLE_PROTECTED_ROOTS_ENV not in message
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(life_dir)
    with pytest.raises(RoleControlStateWriteDenied):
        write_continuous_config(life_dir, enabled=False, objective=OBJECTIVE)
    # The same root spelled differently is the same root.
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(life_dir / ".." / "project")

    assert read_continuous_state(life_dir).enabled is True


def test_each_refused_attempt_is_reported_once(tmp_path, monkeypatch):
    life_dir = _armed(tmp_path / "project")
    _as_role_of(monkeypatch, life_dir)

    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(life_dir)
    # Reading is never reported.
    read_continuous_state(life_dir)
    read_continuous_state(life_dir)
    (event,) = RuntimeIncidentStore(life_dir).pending_events()
    assert event["type"] == "life.runtime.incident.escalated"
    assert event["detector"] == "control_state_guard"
    assert event["manager_attention_required"] is True
    assert "operator or the Manager" in event["reason"]
    RuntimeIncidentStore(life_dir).acknowledge_event(event["incident_id"], event["event_revision"])
    assert RuntimeIncidentStore(life_dir).pending_events() == []

    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(life_dir)
    assert len(RuntimeIncidentStore(life_dir).pending_events()) == 1


def test_role_process_cannot_stop_drain_or_command_its_own_daemon(tmp_path, monkeypatch):
    life_dir = tmp_path / "project"
    life_dir.mkdir()
    _as_role_of(monkeypatch, life_dir)

    with pytest.raises(RoleControlStateWriteDenied):
        request_daemon_control_stop(life_dir, pid=4242, started_at_iso="2026-10-09T00:00:00Z", drain=False)
    with pytest.raises(RoleControlStateWriteDenied):
        request_daemon_drain(life_dir, pid=4242)
    with pytest.raises(RoleControlStateWriteDenied):
        daemon_state.request_daemon_stop(life_dir)
    with pytest.raises(RoleControlStateWriteDenied):
        daemon_state.stop_daemon(life_dir)
    with pytest.raises(RoleControlStateWriteDenied):
        submit_daemon_command(life_dir, operation="stop")
    assert not (life_dir / "daemon.stop-request.json").exists()
    assert not (life_dir / "daemon.drain-request.json").exists()


def test_role_process_may_run_argus_on_an_unrelated_or_nested_project(tmp_path, monkeypatch):
    own = _armed(tmp_path / "project")
    _as_role_of(monkeypatch, own)

    # A scratch project of its own, nested in its workspace, and an unrelated one.
    for other in (tmp_path / "project-work" / "scratch-argus", tmp_path / "elsewhere"):
        write_continuous_config(other, enabled=True, objective="benchmark subtask")
        current = read_continuous_state(other)
        assert compare_and_swap_continuous_config(
            other, expected=current, enabled=False, objective=current.objective,
        )
        disable_continuous_config(other, done_reason="benchmark finished")
        assert read_continuous_state(other).done_reason == "benchmark finished"
        request_daemon_drain(other, pid=4242)
        submit_daemon_command(other, operation="stop")
        assert RuntimeIncidentStore(other).pending_events() == []
    assert read_continuous_state(own).enabled is True


def test_a_role_started_by_a_role_keeps_its_parents_roots(tmp_path, monkeypatch):
    own = _armed(tmp_path / "project")
    _as_role_of(monkeypatch, own)
    monkeypatch.setattr(guard, "_ORCHESTRATED", {})  # a nested tool process registers nothing

    child = mark_role_process_env(None, "subagent")
    assert child[ROLE_PROCESS_ENV] == "subagent"
    assert protected_roots(child) == protected_roots()
    for key, value in child.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(own)


def test_an_orchestrator_started_inside_a_role_manages_its_own_root(tmp_path, monkeypatch):
    parent = _armed(tmp_path / "parent")
    nested = _armed(tmp_path / "parent-work" / "nested")
    _as_role_of(monkeypatch, parent, nested)

    # Before the nested daemon or web server boots, both roots are protected.
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(nested)
    release_role_marker_for(nested)
    disable_continuous_config(nested, done_reason="nested campaign done")
    assert read_continuous_state(nested).done_reason == "nested campaign done"
    # Its parent's project stays out of reach.
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(parent)
    # Releasing the last protected root clears the marker entirely.
    release_role_marker_for(parent)
    assert ROLE_PROCESS_ENV not in os.environ and ROLE_PROTECTED_ROOTS_ENV not in os.environ
    disable_continuous_config(parent)


def test_a_web_server_started_inside_a_role_releases_its_global_root(tmp_path, monkeypatch):
    from argus.webapi import server

    project = _armed(tmp_path / "global" / "projects" / "p1")
    outside = _armed(tmp_path / "outer")
    _as_role_of(monkeypatch, project, outside)
    calls: list[object] = []

    class _Uvicorn:
        @staticmethod
        def run(app, **kwargs):
            calls.append(app)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _Uvicorn)
    monkeypatch.setattr(server, "create_app", lambda **kwargs: "app")
    monkeypatch.setattr("argus.core.runtime_identity.source_root_preflight_error", lambda: "")
    monkeypatch.setattr("argus.core.runtime_identity.release_match_preflight_error", lambda: "")
    monkeypatch.setattr(server, "_uvicorn_log_config", lambda _uvicorn: None)

    assert server.serve(global_root=tmp_path / "global") == 0
    assert calls == ["app"]
    disable_continuous_config(project)
    with pytest.raises(RoleControlStateWriteDenied):
        disable_continuous_config(outside)


def test_argus_writers_outside_role_processes_keep_working(tmp_path):
    life_dir = _armed(tmp_path / "project")
    orchestrate_state_root(life_dir)
    current = read_continuous_state(life_dir)
    assert compare_and_swap_continuous_config(
        life_dir, expected=current, enabled=False, objective=current.objective,
    )
    disable_continuous_config(life_dir, done_reason="operator hold")
    assert read_continuous_state(life_dir).done_reason == "operator hold"
    # No seal or trusted copy: the file is plain campaign state.
    assert set(json.loads((life_dir / "continuous.json").read_text(encoding="utf-8"))) <= {
        "enabled", "objective", "open_ended", "generation", "done_reason", "done_at",
    }
    assert not (life_dir / ".argus").exists()


def test_marked_environment_inherits_everything_else(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_CONTROL_GUARD_PROBE", "kept")
    orchestrate_state_root(tmp_path)
    marked = mark_role_process_env(None)
    assert marked[ROLE_PROCESS_ENV] == "agent"
    assert marked["ARGUS_CONTROL_GUARD_PROBE"] == "kept"
    assert protected_roots(marked) == [os.path.normcase(str(tmp_path.resolve()))]
    assert ROLE_PROCESS_ENV not in os.environ


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub")
def test_agent_cli_child_runs_as_a_marked_role_process(tmp_path):
    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus.agent_cli.runner_backend import BACKEND_CURSOR

    orchestrate_state_root(tmp_path / "project")
    executable = tmp_path / "agent"
    executable.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        "printf '%s\\n' '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"s\"}'\n"
        "printf '{\"type\":\"result\",\"subtype\":\"success\",\"session_id\":\"s\","
        "\"is_error\":false,\"result\":\"marker=%s roots=%s\"}\\n' "
        "\"$ARGUS_SKILL_ROLE_PROCESS\" \"$ARGUS_SKILL_ROLE_PROTECTED_ROOTS\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = AgentCliRunner(str(executable), backend=BACKEND_CURSOR).run_exec(
        prompt="report", resume_thread_id=None, options=RunnerOptions(),
    )

    assert result.last_agent_message == f"marker=agent roots={(tmp_path / 'project').resolve()}"
