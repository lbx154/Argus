"""A cycle whose Planner-visible inputs did not move must not pay for a model call.

The 48-hour billing forensics counted 1,237 ``life.planner.start`` events with
45% producing nothing: the run loop re-asked the Planner after every backoff
even when the backlog, journal, research plan and operator context were
byte-identical to the previous call — one project asked every 107
seconds. The intake now fingerprints exactly those inputs after every real
Planner call that added no work and named who acts next, and an unchanged
fingerprint keeps that answer instead of buying it again. Elapsed time alone is
not an input: a call on the same inputs can only repeat its answer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.apps._inbox import queue_inbox_message
from argus.apps._inbox_delivery import DurableInboxReceiver
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR
from argus.messaging.inbox import PeerAwareInbox
from argus.planner import PlannerVerdict
from argus.skills.vertical_select import persist_vertical


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


def _idle_verdict() -> PlannerVerdict:
    return PlannerVerdict(
        project_done=False,
        reason="the long training run is still producing its result",
        handoff="engineer",
    )


def _install_planner(monkeypatch, verdicts=None):
    calls = {"count": 0}

    def _plan_next(_planner, **_kwargs):
        calls["count"] += 1
        if verdicts:
            return verdicts.pop(0)
        return _idle_verdict()

    monkeypatch.setattr("argus.planner.Planner.plan_next", _plan_next)
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
        if event.get("type") == "life.planner.idle"
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


def test_elapsed_time_alone_never_buys_a_planner_call(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1
    later = time.monotonic() + 7 * 24 * 3600.0
    monkeypatch.setattr(time, "monotonic", lambda: later)
    monkeypatch.setattr(time, "time", lambda: later)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1


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


def test_subagent_report_does_not_bypass_the_skip(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software", workflow_mode="direct")
    life = tmp_path / "life"
    life.mkdir()
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(
        project,
        life,
        user_inbox=PeerAwareInbox(
            life,
            operator_receiver=DurableInboxReceiver(
                life, consumer="supervisor", project_root=project,
            ),
        ),
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1

    report = "## Subagent Report: train-01\n**Event**: COMPLETED\n\n- exit_code: 0\n"
    queue_inbox_message(life, report, source="subagent")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1, "a host report is not operator input"

    # The report was released, not consumed: the next real Planner turn reads it.
    receiver = DurableInboxReceiver(life, consumer="supervisor", project_root=project)
    assert [str(text) for text in receiver.receive(mission_id="")] == [report.strip()]
    receiver.release_pending()


def test_backend_failures_never_arm_the_skip(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(
        monkeypatch,
        verdicts=[
            PlannerVerdict(
                project_done=False,
                reason="planner backend failed before producing output; will retry later",
                error="backend exit 1",
            ),
            _idle_verdict(),
        ],
    )
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_ERROR
    assert calls["count"] == 1

    # A failed call may succeed on retry with the same inputs; it must not be
    # answered from a decision that was never made.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2


def test_an_unusable_answer_is_not_bought_again_on_unchanged_inputs(
    tmp_path: Path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    unusable = "planner said not done but produced no concrete tasks"
    calls = _install_planner(
        monkeypatch,
        verdicts=[
            PlannerVerdict(project_done=False, reason=unusable, error=unusable)
            for _ in range(3)
        ],
    )
    supervisor = _supervisor(project, life)

    supervisor._plan_next_work()
    supervisor._plan_next_work()
    asked = calls["count"]
    supervisor._plan_next_work()

    assert calls["count"] == asked


@pytest.mark.parametrize("declared", [False, True])
def test_hours_waiting_on_an_answer_cost_no_more_planner_calls(
    tmp_path: Path, monkeypatch, declared: bool
) -> None:
    """The daemon returns at least every IDLE_BACKOFF_CAP_SECONDS while a
    question waits on the operator. A return is not news: on unchanged inputs it
    buys no Planner turn, whether a mission asked or the Planner asked itself
    (its new card is read once, then the same question changes nothing)."""
    from argus.life.supervisor._constants import IDLE_BACKOFF_CAP_SECONDS

    project = tmp_path / "project"
    project.mkdir()
    verdict = PlannerVerdict(
        project_done=False,
        reason="the dataset choice is the operator's",
        handoff="operator",
        operator_question="Which dataset should I use?",
    ) if declared else _idle_verdict()
    calls = _install_planner(monkeypatch, verdicts=[verdict] * 36)
    supervisor = _supervisor(project, tmp_path / "life")
    if not declared:
        # A mission parked on its card; a declared question parks the plan itself.
        item = supervisor.memory.backlog.add(BacklogItem.new(
            title="Choose the dataset", objective="Train on the dataset the operator picks.",
        ))
        supervisor.memory.backlog.update(
            item.id, status="paused_operator", pending_question="Which dataset should I use?",
        )

    start = time.time()
    for cycle in range(36):  # three hours of returns at the backoff cap
        now = start + cycle * IDLE_BACKOFF_CAP_SECONDS
        monkeypatch.setattr(time, "time", lambda now=now: now)
        monkeypatch.setattr(time, "monotonic", lambda now=now: now)
        supervisor.run()

    assert calls["count"] == (2 if declared else 1)


def test_an_old_waiting_contract_file_is_ignored(tmp_path: Path, monkeypatch) -> None:
    """An older release persisted the Planner's wait; nothing reads it now."""
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    life.mkdir()
    (life / "planner-waiting-contract-0123abcd.json").write_text(json.dumps({
        "active": True,
        "blocker_fingerprint": "unobserved",
        "operator_action_required": True,
        "expires_at": 1.0,
    }), encoding="utf-8")
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1
