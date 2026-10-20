"""A stall guard's guess asks the Manager instead of ending a supervised mission.

The count guards (empty Engineer turns, no-progress verdicts, the soft and
hard round limits, the decision clock) only guess that progress stopped. Under
a Manager the guess asks once per set of facts and the mission runs on the
ruling: asked again on the same facts the Manager could only repeat itself.
Without a Manager (the CLI) the guard still ends the run.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import MemoryBackend
from argus.core.event_catalog import EventType
from argus.core.models import ReviewDecision, RunnerResult
from argus.engineer.round_state import EngineerTurnOutcome, RoundLoopState
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.manager import supervision
from argus.reviewer import Reviewer, ReviewerConfig


@pytest.fixture()
def mission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "solver.py").write_text("print('v1')\n", encoding="utf-8")
    packet = tmp_path / "handoffs" / "m1" / "context.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps({"mission_id": "m1"}), encoding="utf-8")
    asked: list[str] = []
    monkeypatch.setattr(supervision, "_admit", lambda _m, _r, event: asked.append(event["consult_reason"]) or True)
    manager = SimpleNamespace(runner=SimpleNamespace(fork=lambda: None))
    engineer = SupervisedEngineer(
        engineer_runner=MemoryBackend(),
        reviewer=Reviewer(runner=MemoryBackend()),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    state = RoundLoopState()
    rounds = iter(range(1, 100))
    seen: list[dict] = []

    def settle(*, manager_supervised: bool = True, progress: bool | None = False):
        events: list[dict] = []
        control = engineer._settle_round(
            review=ReviewDecision(
                status="continue", reason="The residual did not move.", next_action="Try another route.",
                planner_report={} if progress is None else {"forward_progress": progress},
            ),
            round_index=next(rounds),
            supervised_config=SupervisedConfig(
                manager_supervised=manager_supervised,
                decision_progress_timeout_seconds=0,
                context_packet_path=str(packet),
                operator_question_policy_root=tmp_path / "state",
            ),
            workdir=workdir,
            outcome=EngineerTurnOutcome(
                engineer_result=RunnerResult(exit_code=0), round_thread_id=None, fatal_error=None,
                safe_fatal_error=None, stop_kind=None, raw_engineer_message="Tried again.",
                engineer_message="Tried again.", process_ownership_note="", round_started_at=0.0,
            ),
            state=state,
            review_completed_hook=None,
            continue_adaptor=None,
            on_event=events.append,
        )
        seen.extend(events)
        for event in events:
            if event["type"] == EventType.ROUND_STALL:
                supervision.schedule_supervision(manager, tmp_path / "state", event)
        return control

    return SimpleNamespace(settle=settle, asked=asked, state=state, workdir=workdir, events=seen)


def test_a_tripped_guard_asks_the_manager_once_per_set_of_facts(mission):
    controls = [mission.settle() for _ in range(10)]

    # The stall guard (4 rounds) held for seven of them; none ended the mission.
    assert mission.state.semantic_stall_streak == 10
    assert all(control.terminal is None for control in controls)
    assert mission.asked == ["2 rounds without forward progress"]

    (mission.workdir / "solver.py").write_text("print('v2')\n", encoding="utf-8")
    assert all(mission.settle().terminal is None for _ in range(5))
    assert mission.asked == [
        "2 rounds without forward progress",
        "Reviewer reported no forward progress for 11 consecutive rounds.",
    ]


def test_an_empty_engineer_guard_asks_rather_than_ends(mission):
    mission.state.no_progress_streak = 5

    assert all(mission.settle(progress=None).terminal is None for _ in range(10))
    assert mission.asked == ["Engineer produced no effective output for 5 consecutive rounds."]


def test_without_a_manager_the_guard_still_ends_the_run(mission):
    controls = [mission.settle(manager_supervised=False) for _ in range(4)]

    assert [control.terminal is None for control in controls] == [True, True, True, False]
    status, _rounds, _message, reason, _thread = controls[-1].terminal
    assert status == "no_progress"
    assert reason.startswith("Reviewer reported no forward progress for 4 consecutive rounds.")
    assert mission.asked == []


def test_a_supervised_review_names_the_ledger_its_periodic_look_reads(mission):
    mission.settle()
    mission.settle(manager_supervised=False)

    reviews = [event for event in mission.events if event["type"] == EventType.ROUND_REVIEW_COMPLETED]
    assert reviews[0]["holder_ledger"].endswith("lineage-m1/holders.json")
    assert "holder_ledger" not in reviews[1]
