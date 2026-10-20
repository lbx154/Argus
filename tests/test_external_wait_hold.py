"""An Engineer waiting on its own job keeps the mission slot while nobody else needs it."""
from __future__ import annotations

import json
import os
from typing import Any, cast

from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer import round_signals
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import ReviewerConfig


def job(workdir, *, state="running"):
    root = workdir / ".argus_subagents"
    root.mkdir(exist_ok=True)
    (root / "grid.json").write_text(json.dumps({
        "task_id": "grid", "run_id": "run-1", "state": state,
        "mode": "direct", "worker_pid": os.getpid(), "heartbeat_at": 100,
    }))


class Engineer:
    def __init__(self):
        self.calls = 0

    def run_exec(self, **kwargs):
        self.calls += 1
        return RunnerResult(exit_code=0, agent_messages=[
            'Launched the calibrated grid on GPU 1.\n{"wait_for": "subagent", "wait_id": "grid"}'
        ])


class Reviewer:
    def __init__(self):
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        return ReviewDecision(status="done", reason="Checked the grid result.", next_action="")


def execute(tmp_path, monkeypatch, *, hold, finish_during_cadence, events=None):
    cadences: list[str] = []

    def cadence(workdir, work_id):
        cadences.append(work_id)
        if len(cadences) == finish_during_cadence:
            job(tmp_path, state="done")
            return "done", 20.0
        return "cadence_elapsed", 120.0

    monkeypatch.setattr(round_signals, "wait_for_external_work_cadence", cadence)
    engineer, reviewer = Engineer(), Reviewer()
    value = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    value.engineer_runner = engineer
    value.engineer_config = EngineerConfig(model="stub")
    value.reviewer = reviewer
    value.reviewer_config = ReviewerConfig(model="stub")
    config = SupervisedConfig(
        max_rounds=4, background_subagent_advisory=True, external_wait_hold=hold,
        context_packet_path=tmp_path / "handoff" / "mission.json",
    )
    events = [] if events is None else events
    result = value.run(
        objective="Measure the calibrated GEMM grid", workdir=tmp_path,
        engineer_prompt_builder=lambda action, include_static=True: action or "Start",
        supervised_config=config, on_event=events.append,
    )
    return result, cadences, engineer, reviewer


def test_round_keeps_waiting_while_the_daemon_has_no_other_use_for_the_slot(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", raising=False)
    job(tmp_path)
    result, cadences, engineer, reviewer = execute(
        tmp_path, monkeypatch, hold=lambda: True, finish_during_cadence=3,
    )
    # Three cadences in one session, then the result is used and reviewed:
    # no pause, no daemon resume, no second Engineer session.
    assert cadences == ["grid"] * 3
    assert result[0] == "done" and engineer.calls == 2 and reviewer.calls == 1


def test_a_ten_minute_wait_writes_what_a_short_one_does(tmp_path, monkeypatch):
    """Waiting is silent: five two-minute cadences add nothing to the journal."""
    monkeypatch.delenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", raising=False)
    journals = []
    for cadences in (1, 6):
        workdir = tmp_path / str(cadences)
        workdir.mkdir()
        job(workdir)
        events: list[dict] = []
        result, *_ = execute(
            workdir, monkeypatch, hold=lambda: True, finish_during_cadence=cadences, events=events,
        )
        assert result[0] == "done"
        journals.append([event["type"] for event in events])
    assert journals[0] == journals[1]
    assert not any(kind.startswith("round.external_work_wait") for kind in journals[1])


def test_round_releases_the_slot_as_soon_as_the_daemon_wants_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", raising=False)
    job(tmp_path)
    answers = iter([True, False])
    result, cadences, engineer, _ = execute(
        tmp_path, monkeypatch, hold=lambda: next(answers), finish_during_cadence=9,
    )
    assert len(cadences) == 2
    assert result[0] == "paused_external_work" and engineer.calls == 1
    assert "released the mission slot" in result[3]


def test_without_an_answer_from_the_daemon_the_round_pauses_after_one_cadence(tmp_path, monkeypatch):
    job(tmp_path)
    result, cadences, _, _ = execute(tmp_path, monkeypatch, hold=None, finish_during_cadence=9)
    assert len(cadences) == 1 and result[0] == "paused_external_work"


def test_hold_budget_and_a_failing_answer_both_release_the_slot(tmp_path, monkeypatch):
    job(tmp_path)
    monkeypatch.setenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS", "0")
    result, cadences, _, _ = execute(tmp_path, monkeypatch, hold=lambda: True, finish_during_cadence=9)
    assert len(cadences) == 1 and result[0] == "paused_external_work"
    monkeypatch.delenv("ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS")

    def broken():
        raise RuntimeError("backlog unreadable")

    result, cadences, _, _ = execute(tmp_path, monkeypatch, hold=broken, finish_during_cadence=9)
    assert len(cadences) == 1 and result[0] == "paused_external_work"
