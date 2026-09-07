"""A late Planner host failure may disarm only the generation that called it."""

from types import SimpleNamespace

import pytest

from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR, PLAN_RETRY
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.planner import Planner, PlannerVerdict

HOST_ERROR = (
    "Code Mode is unavailable because failed to spawn code-mode host "
    "/codex/host: host executable was not found; fail closed"
)


def _supervisor(tmp_path, monkeypatch):
    memory = LifeMemory.open(tmp_path / "project")
    events = []
    supervisor = LifeSupervisor(
        memory=memory, runner=SimpleNamespace(), planner_runner=object(),
        sink=SimpleNamespace(handle_event=events.append),
        config=LifeSupervisorConfig(continuous=True, continuous_objective="original campaign"),
    )
    for name in (
        "_render_research_plan_for_planner", "_planner_runtime_with_idle_note",
        "_stuck_subagent_families_note", "_manager_intent_prompt_block",
        "_planner_authorization_prompt_block",
    ):
        monkeypatch.setattr(supervisor, name, lambda *args: "")
    monkeypatch.setattr(supervisor, "_planner_journal_window", lambda: None)
    monkeypatch.setattr(supervisor, "_render_journal_delta_for_planner", lambda _: ("", None))
    monkeypatch.setattr(supervisor, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_apply_research_plan_update", lambda _: None)
    return supervisor, events


@pytest.mark.parametrize("change", [None, "new objective", "rearm", "operator hold"])
def test_late_host_failure_preserves_newer_operator_generation(tmp_path, monkeypatch, change):
    supervisor, events = _supervisor(tmp_path, monkeypatch)
    root = supervisor.memory.root
    write_continuous_config(root, enabled=True, objective="original campaign")
    before = read_continuous_state(root)
    after_operator = []

    def plan_next(self, **kwargs):
        assert kwargs["continuous_objective"] == "original campaign"
        if change:
            write_continuous_config(
                root, enabled=change != "operator hold",
                objective="replacement campaign" if change == "new objective" else "original campaign",
                done_reason="operator paused this" if change == "operator hold" else "",
            )
        after_operator.append(read_continuous_state(root))
        return PlannerVerdict(project_done=False, reason="host unavailable", error=HOST_ERROR)

    monkeypatch.setattr(Planner, "plan_next", plan_next)
    state = _PlanCycleState(None)
    assert supervisor._pc_invoke_planner(state) is None
    assert state.planner_continuous_state == before
    result = supervisor._pc_normalize_verdict(state)
    after = read_continuous_state(root)
    alerts = [event for event in events if event.get("type") == "life.planner.error"]
    if change:
        assert result == PLAN_RETRY
        assert after == after_operator[0]
        assert not alerts
    else:
        assert result == PLAN_AWAITING
        assert after.objective == before.objective
        assert not after.enabled
        assert after.done_reason == HOST_ERROR
        assert len(alerts) == 1


def test_planner_pause_uses_project_config_without_changing_global_state(tmp_path, monkeypatch):
    supervisor, _events = _supervisor(tmp_path, monkeypatch)
    project = supervisor.memory.root
    global_root = tmp_path / "global"
    write_continuous_config(global_root, enabled=True, objective="unrelated campaign")
    write_continuous_config(project, enabled=True, objective="original campaign")
    before_global = read_continuous_state(global_root)
    supervisor.memory = SimpleNamespace(
        root=global_root, project_root=project, backlog=supervisor.memory.backlog,
    )
    monkeypatch.setattr(Planner, "plan_next", lambda *args, **kwargs: PlannerVerdict(
        project_done=False, reason="host unavailable", error=HOST_ERROR,
    ))
    state = _PlanCycleState(None)
    assert supervisor._pc_invoke_planner(state) is None
    assert supervisor._pc_normalize_verdict(state) == PLAN_AWAITING
    assert not read_continuous_state(project).enabled
    assert read_continuous_state(global_root) == before_global


def test_stale_loop_objective_does_not_invoke_a_newer_campaign(tmp_path, monkeypatch):
    supervisor, events = _supervisor(tmp_path, monkeypatch)
    root = supervisor.memory.root
    write_continuous_config(root, enabled=True, objective="new campaign")
    before = read_continuous_state(root)
    calls = []
    monkeypatch.setattr(Planner, "plan_next", lambda *args, **kwargs: calls.append(kwargs))
    assert supervisor._pc_invoke_planner(_PlanCycleState(None)) == PLAN_RETRY
    assert not calls
    assert read_continuous_state(root) == before
    assert not events


def test_existing_operator_hold_is_not_rewritten_as_host_failure(tmp_path, monkeypatch):
    supervisor, events = _supervisor(tmp_path, monkeypatch)
    root = supervisor.memory.root
    write_continuous_config(
        root, enabled=False, objective="original campaign", done_reason="operator paused",
    )
    before = read_continuous_state(root)
    assert supervisor._pause_planner_execution_host(HOST_ERROR, expected=before) == PLAN_AWAITING
    assert read_continuous_state(root) == before
    assert not events


def test_failed_pause_cas_does_not_emit_a_successful_pause(tmp_path, monkeypatch):
    from argus_skill.daemon import state as state_module

    supervisor, events = _supervisor(tmp_path, monkeypatch)
    root = supervisor.memory.root
    write_continuous_config(root, enabled=True, objective="original campaign")
    before = read_continuous_state(root)
    monkeypatch.setattr(state_module, "compare_and_swap_continuous_config", lambda *args, **kwargs: False)
    assert supervisor._pause_planner_execution_host(HOST_ERROR, expected=before) == PLAN_RETRY
    assert read_continuous_state(root) == before
    assert not events


@pytest.mark.parametrize("error", ["ordinary backend failure", "blocked by content filtering"])
def test_non_host_planner_errors_keep_existing_policy(tmp_path, monkeypatch, error):
    supervisor, events = _supervisor(tmp_path, monkeypatch)
    root = supervisor.memory.root
    write_continuous_config(root, enabled=True, objective="original campaign")
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(project_done=False, reason=error, error=error)
    assert supervisor._pc_normalize_verdict(state) == PLAN_ERROR
    assert read_continuous_state(root).enabled is (error == "ordinary backend failure")
    alerts = [event for event in events if event.get("type") == "life.planner.error"]
    assert len(alerts) == 1
    if error == "blocked by content filtering":
        assert alerts[0]["stop_kind"] == "permanent_error"
    else:
        assert "stop_kind" not in alerts[0]
