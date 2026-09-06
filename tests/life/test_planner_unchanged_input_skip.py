"""A cycle whose Planner-visible inputs did not move must not pay for a model call.

The 48-hour billing forensics counted 1,237 ``life.planner.start`` events with
45% producing nothing: the run loop re-asked the Planner after every backoff
even when the backlog, journal, research plan, operator context, and persisted
wait were byte-identical to the previous call — one project asked every 107
seconds. The intake now fingerprints exactly those inputs after every real
Planner call that answered with an intentional wait, and an unchanged
fingerprint keeps that answer instead of buying it again. Time stays an input:
after ``PLANNER_UNCHANGED_SKIP_MAX_SECONDS`` the Planner is called regardless.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    OPERATOR_WAIT_TURN_REGRANT_SECONDS,
    PLAN_AWAITING,
    PLAN_ERROR,
    PLANNER_UNCHANGED_SKIP_MAX_SECONDS,
)
from argus_skill.planner import PlannerVerdict, WaitingContract


class _Runner:
    pass


def _supervisor(project: Path, life: Path, **config_overrides) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    config = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="keep improving the solver",
        open_ended=False,
        final_certification_gate=False,
        project_worktree=project,
        artifact_root=project,
        **config_overrides,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=config,
        planner_runner=object(),
    )
    supervisor._vertical_resolved = True
    return supervisor


def _waiting_verdict() -> PlannerVerdict:
    return PlannerVerdict(
        project_done=False,
        reason="the long training run is still producing its result",
        waiting=True,
        waiting_reason="the long training run is still producing its result",
    )


def _install_planner(monkeypatch, verdicts=None):
    calls = {"count": 0}

    def _plan_next(_planner, **_kwargs):
        calls["count"] += 1
        if verdicts:
            return verdicts.pop(0)
        return _waiting_verdict()

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    return calls


def _events(life: Path) -> list[dict]:
    path = life / "events.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def test_unchanged_inputs_skip_the_model_call(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1, "an unchanged cycle must reuse the last decision"

    skipped = [
        event
        for event in _events(life)
        if event.get("type") == "life.planner.waiting"
        and event.get("model_call_skipped") is True
    ]
    assert skipped, "the skipped cycle must still be visible in the event log"
    assert "without another model call" in str(skipped[-1].get("reason") or "")


def test_new_backlog_evidence_calls_the_planner_again(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Reproduce the baseline",
            objective="Run the baseline once and record the score.",
        )
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2, "a changed backlog is new evidence for the Planner"


def test_new_settled_mission_calls_the_planner_again(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    supervisor.sink.handle_event({
        "type": "life.mission.completed",
        "item_id": "item-b",
        "title": "Baseline reproduced",
        "objective": "Run the baseline once.",
        "success": True,
        "status": "done",
        "summary": "The baseline score matches the paper.",
    })

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2, "a settled mission is new evidence for the Planner"


def test_unchanged_skip_expires_after_the_time_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1
    supervisor._planner_unchanged_skip_armed_at = (
        time.monotonic() - PLANNER_UNCHANGED_SKIP_MAX_SECONDS - 1.0
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2, "elapsed time alone must force a real Planner call"

    # The forced call answered with the same wait on the same inputs, so the
    # window re-arms and the following cycle skips again.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2


def test_operator_message_bypasses_the_skip(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    inbox = ["please switch to the smaller model"]
    supervisor = _supervisor(
        project,
        life,
        user_inbox=lambda: inbox.pop(0) if inbox else None,
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2, "fresh operator words always reach the Planner"


def test_operator_wait_turn_regrant_beats_the_skip(
    tmp_path: Path, monkeypatch
) -> None:
    """A turn the wait machinery grants is never answered from memory.

    The event-wait path deliberately re-grants one Planner call per
    ``OPERATOR_WAIT_TURN_REGRANT_SECONDS`` on an operator-only wait. That grant
    IS the decision to spend a model call; the unchanged-input skip must stand
    aside even though nothing it fingerprints has moved.
    """
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = {"count": 0}

    def _plan_next(_planner, **_kwargs):
        calls["count"] += 1
        return PlannerVerdict(
            project_done=False,
            reason="venue submission needs operator authorization",
            waiting=True,
            waiting_reason="venue submission needs operator authorization",
            waiting_contract=WaitingContract(
                blocker_fingerprint="submission_needs_operator_8d2b840c",
                recheck_condition="operator authorizes venue submission",
                recheck_token="token-v1",
                wait_mode="event",
                wake_on=("authorization",),
                operator_action_required=True,
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1
    # The contract's one idle-capacity turn.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2
    # Turn spent, cadence not reached: the wait machinery itself declines.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2

    wait_path = next(life.glob("planner-waiting-contract-*.json"))
    wait_state = json.loads(wait_path.read_text(encoding="utf-8"))
    wait_state["idle_capacity_turn_ts"] = (
        time.time() - OPERATOR_WAIT_TURN_REGRANT_SECONDS - 1
    )
    supervisor._write_planner_waiting_contract_state(wait_state)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 3, "the re-granted turn must reach the model"


def test_planner_errors_never_arm_the_skip(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(
        monkeypatch,
        verdicts=[
            PlannerVerdict(
                project_done=False,
                reason="backend failed",
                error="backend exit 1",
            ),
            _waiting_verdict(),
        ],
    )
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_ERROR
    assert calls["count"] == 1

    # A failed call may succeed on retry with the same inputs; it must not be
    # answered from a decision that was never made.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2
