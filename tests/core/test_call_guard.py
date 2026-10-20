"""Background model calls in a daemon need progress between them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.core import call_guard
from argus.core.call_guard import BACKGROUND_CALL_BUDGET
from argus.core.event_catalog import EventType


@pytest.fixture(autouse=True)
def _guard_off_after_each_test():
    yield
    call_guard.disable()


def _trips() -> list[list[str]]:
    trips: list[list[str]] = []
    call_guard.enable(on_trip=trips.append)
    return trips


def test_background_calls_stop_after_the_budget_and_the_operator_is_told_once():
    trips = _trips()
    for cycle in range(BACKGROUND_CALL_BUDGET):
        assert call_guard.refusal(f"planner.cycle{cycle}") == ""
    for _ in range(100):
        assert "paused" in call_guard.refusal("manager-route")
    assert len(trips) == 1 and len(trips[0]) == BACKGROUND_CALL_BUDGET


def test_progress_restores_the_budget_and_a_new_trip_is_told_again():
    trips = _trips()
    for _ in range(BACKGROUND_CALL_BUDGET + 1):
        call_guard.refusal("planner.cycle1")
    call_guard.observe({"type": EventType.LIFE_MISSION_COMPLETED})
    assert call_guard.refusal("planner.cycle2") == ""
    for _ in range(BACKGROUND_CALL_BUDGET):
        call_guard.refusal("planner.cycle3")
    assert len(trips) == 2


@pytest.mark.parametrize("label", [
    "engineer-r4", "engineer-r4.winddown", "reviewer", "reviewer-correctness",
    "advisor.engineer.math",
    "peer-message-response", "manager-frontdoor-classify",
])
def test_mission_work_and_answers_to_one_input_are_never_counted(label):
    _trips()
    for _ in range(10 * BACKGROUND_CALL_BUDGET):
        assert call_guard.refusal(label) == ""
    assert call_guard.refusal("planner.cycle1") == ""


def test_only_a_daemon_guards_its_calls():
    for _ in range(10 * BACKGROUND_CALL_BUDGET):
        assert call_guard.refusal("planner.cycle1") == ""


def test_the_event_log_reports_progress(tmp_path):
    from argus.life.event_log import JsonlEventSink

    _trips()
    for _ in range(BACKGROUND_CALL_BUDGET + 1):
        call_guard.refusal("planner.cycle1")
    JsonlEventSink(None, life_dir=tmp_path).handle_event({
        "type": EventType.LIFE_PLANNER_TASK_ADDED, "item_id": "t1", "title": "next",
    })
    assert call_guard.refusal("planner.cycle2") == ""


def test_a_refused_call_never_reaches_the_provider(monkeypatch, tmp_path):
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.agent_cli.models import AgentRunResult
    from argus.core.models import RunnerOptions

    backend = AgentCliBackend(backend="codex")
    spawned: list[str] = []

    def run_exec(**_kwargs):
        spawned.append("call")
        return AgentRunResult(command=["codex"], exit_code=0, agent_messages=["ok"])

    monkeypatch.setattr(backend._runner, "run_exec", run_exec)
    monkeypatch.setattr(call_guard, "BACKGROUND_CALL_BUDGET", 2)
    _trips()
    options = RunnerOptions(working_dir=str(tmp_path))
    results = [
        backend.run_exec(prompt="synthetic fixture", options=options, run_label="manager-letter")
        for _ in range(5)
    ]
    assert len(spawned) == 2
    assert all(result.stop_kind == "operator_pause" for result in results[2:])
    assert "paused" in str(results[-1].fatal_error)
    backend.run_exec(prompt="synthetic fixture", options=options, run_label="engineer-r1")
    assert len(spawned) == 3


@pytest.mark.parametrize("objective,expected", [
    ("keep improving the paper", "background model calls"),
    ("继续改进论文", "后台模型调用"),
])
def test_the_daemon_tells_the_operator_in_their_language(tmp_path, objective, expected):
    from argus.core.transcript import read_turns
    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    statuses: list[str] = []
    supervisor = SimpleNamespace(
        _manager_project_report_root=lambda: tmp_path,
        config=SimpleNamespace(continuous_objective=objective),
        _emit_status=statuses.append,
    )
    LifeWorkerRunMixin._background_calls_paused(
        SimpleNamespace(sup=supervisor), ["planner.cycle7", "planner.cycle8", "manager-letter"],
    )
    turns = [turn for turn in read_turns(tmp_path) if turn.get("role") == "argus"]
    assert len(turns) == 1 and expected in turns[0]["text"]
    assert "planner.cycleN" in turns[0]["text"] and statuses
