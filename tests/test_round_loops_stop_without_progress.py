"""Round loops that make no progress stop instead of running without end.

A venue revision cleared every round ceiling, so rounds the Reviewer judged
fruitless went on for as long as the bar stayed out of reach; and an Engineer
that handed each round back to itself skipped every stop check. Operator
decision, 2026-10-19: five fruitless review rounds stop and ask the operator;
unanswered for twelve hours, the Manager decides.
"""
from __future__ import annotations

import time
from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.venue_review import (
    VENUE_STALL_DECISION_SECONDS,
    VENUE_STALL_ROUNDS,
    configure_venue_revisions,
)
from argus.engineer.round_self_review import ENGINEER_SELF_HANDOFF_LIMIT
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _fruitless_round(backend: MemoryBackend, index: int) -> None:
    backend.queue(f"engineer-r{index}", CannedResponse(message=f"round {index} revised the paper"))
    backend.queue("reviewer", CannedResponse(review_action=(
        "revise_review",
        {"review": "Still below the venue bar.\nTighten the evaluation.", "forward_progress": False},
    )))


def test_a_fruitless_venue_revision_stops_and_asks_the_operator(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for index in range(1, 40):
        _fruitless_round(backend, index)
    config = SupervisedConfig()
    configure_venue_revisions(config)
    assert config.max_rounds == 0 and config.stall_threshold == VENUE_STALL_ROUNDS

    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Bring the paper to the venue bar.",
        engineer_prompt_builder=lambda _next, _static=True: "Revise the paper.",
        supervised_config=config,
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert len(rounds) == VENUE_STALL_ROUNDS
    review = rounds[-1].review
    assert review.review_source == "venue_stall"
    assert f"{VENUE_STALL_ROUNDS} review rounds" in review.operator_question
    assert [option["id"] for option in review.operator_options] == [
        "change_approach", "accept_current", "lower_bar",
    ]


def test_an_engineer_handing_rounds_to_itself_reaches_the_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for index in range(1, 30):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(message=f"round {index}: more to do\n\nNEXT_OWNER=engineer"),
        )
    backend.queue("reviewer", CannedResponse(review_action=(
        "approve_review", {"review": "The objective is verified complete."},
    )))

    status, _rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Finish the task.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(),
        workdir=tmp_path,
    )

    engineer_calls = [label for label, _prompt, _options in backend.history if label.startswith("engineer-r")]
    assert status == "done"
    assert len(engineer_calls) == ENGINEER_SELF_HANDOFF_LIMIT + 1


def _stalled_paper(tmp_path: Path, monkeypatch, *, asked_hours_ago: float):
    from argus.core.operator_decision import build_operator_decision
    from argus.life.event_log import JsonlEventSink
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

    project = tmp_path / "project"
    project.mkdir()
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective="publish the paper",
            project_worktree=project, artifact_root=project,
        ),
        planner_runner=object(),
    )
    item = BacklogItem.new(title="Revise the paper", objective="Reach the venue bar.")
    memory.backlog.add(item)
    question = "The paper has gone 5 review rounds without progress. How should Argus continue?"
    card = build_operator_decision(
        item_id=item.id, title=item.title, reason="Five fruitless rounds.", question=question,
        options=[
            {"id": "change_approach", "label": "Revise with a different approach"},
            {"id": "accept_current", "label": "Accept the current version"},
        ],
    )
    card["decision_kind"] = "venue_stall"
    card["asked_at"] = time.time() - asked_hours_ago * 3600.0
    memory.backlog.update(item.id, status="paused_operator", pending_question=question,
                          operator_decision=card)
    prompts: list[str] = []

    class Manager:
        def write_prose(self, prompt, **_kwargs):
            prompts.append(prompt)
            return "Weighed the reviews.\nOPTION=accept_current\nREASON=The remaining issues are minor."

    monkeypatch.setattr(supervisor, "_bound_manager", lambda: Manager())
    return supervisor, item, prompts


def test_the_manager_decides_a_stall_left_unanswered_for_twelve_hours(tmp_path, monkeypatch) -> None:
    from argus.core.autonomy import read_autonomous_assumptions
    from argus.core.transcript import read_turns

    assert VENUE_STALL_DECISION_SECONDS == 12 * 3600.0
    supervisor, item, prompts = _stalled_paper(tmp_path, monkeypatch, asked_hours_ago=13)

    assert supervisor._decide_overdue_venue_stalls() is True
    for _ in range(5):
        assert supervisor._decide_overdue_venue_stalls() is False
    assert len(prompts) == 1
    active = {row.id: row for row in supervisor.memory.backlog.active()}
    assert item.id not in active or active[item.id].status != "paused_operator"
    continuation = [row for row in active.values() if row.id != item.id]
    assert continuation and "Accept the current version" in continuation[0].objective
    assumptions = read_autonomous_assumptions(supervisor._project_state_root())
    assert any(row.get("source") == "venue_stall_timeout" for row in assumptions)
    notices = [
        turn for turn in read_turns(supervisor._manager_project_report_root())
        if str(turn.get("message_id") or "").startswith("argus-notice-venue-stall-decided")
    ]
    assert len(notices) == 1


def test_a_recent_stall_stays_with_the_operator(tmp_path, monkeypatch) -> None:
    supervisor, item, prompts = _stalled_paper(tmp_path, monkeypatch, asked_hours_ago=11)
    assert supervisor._decide_overdue_venue_stalls() is False
    assert prompts == []
    active = {row.id: row for row in supervisor.memory.backlog.active()}
    assert active[item.id].status == "paused_operator"


def test_a_second_stall_after_the_managers_choice_waits_for_the_operator(
    tmp_path, monkeypatch,
) -> None:
    from argus.core.autonomy import record_autonomous_assumption
    from argus.core.transcript import append_turn, read_turns

    supervisor, item, prompts = _stalled_paper(tmp_path, monkeypatch, asked_hours_ago=13)
    record_autonomous_assumption(
        supervisor._project_state_root(), item_id="earlier", conflict="earlier stall",
        reading="Revise with a different approach", source="venue_stall_timeout",
    )

    assert supervisor._decide_overdue_venue_stalls() is False
    assert prompts == []
    active = {row.id: row for row in supervisor.memory.backlog.active()}
    assert active[item.id].status == "paused_operator"
    notices = [
        turn for turn in read_turns(supervisor._manager_project_report_root())
        if str(turn.get("message_id") or "").startswith("argus-notice-venue-stall-waiting")
    ]
    assert len(notices) == 1

    # Once the operator speaks again, the Manager may decide the next stall.
    time.sleep(0.01)
    append_turn(supervisor._manager_project_report_root(), "operator", "keep going")
    card = dict(active[item.id].operator_decision)
    card.pop("auto_decision_attempted_at")
    supervisor.memory.backlog.update(item.id, operator_decision=card)
    assert supervisor._decide_overdue_venue_stalls() is True
    assert len(prompts) == 1
