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
from types import SimpleNamespace

import pytest

from argus.apps._inbox import queue_inbox_message
from argus.apps._inbox_delivery import DurableInboxReceiver
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory, park_reason, planner_may_retire_park
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR
from argus.messaging.inbox import PeerAwareInbox
from argus.planner import PlannerVerdict, TaskSpec
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

    # Waiting is said once, not on every pass that finds nothing new.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert [event for event in _events(life) if event.get("model_call_skipped") is True] == skipped


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


def test_a_manager_hold_wakes_the_planner_to_plan_the_next_step(
    tmp_path: Path, monkeypatch
) -> None:
    """Step 1 settled, and the Planner handed the stage to the Manager instead
    of planning step 2. The Manager's HOLD and its reason are new to the
    Planner, so it is asked again and plans step 2 rather than both waiting on
    each other (2026-10-20: the HOLD was dropped and the project went silent).
    """
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch, [
        PlannerVerdict(
            project_done=False,
            reason="Step 1 passed review; the original task has ended.",
            handoff="manager",
        ),
        PlannerVerdict(
            project_done=False,
            reason="Step 2 is next.",
            new_tasks=[TaskSpec(title="Step 2: to_camel", objective="Add to_camel(s) with tests.")],
        ),
    ])
    supervisor = _supervisor(project, life)
    held: list[dict] = []

    class _Manager:
        def decide_stage_transition(self, **kwargs):  # noqa: ANN003
            held.append(kwargs)
            return SimpleNamespace(
                action="hold", target_stage="", reason="Step 1 is done; plan step 2 now.",
                current_stage="", source="manager_llm", diagnostic="intentional_hold",
            )

    supervisor._bound_manager = lambda: _Manager()  # type: ignore[method-assign]
    supervisor.sink.handle_event({
        "type": "life.mission.completed",
        "item_id": "step-1",
        "title": "Step 1: to_snake",
        "objective": "Add to_snake(s) with tests.",
        "success": True,
        "status": "done",
        "summary": "to_snake passes review.",
    })

    supervisor._plan_next_work()
    supervisor._plan_next_work()

    assert len(held) == 1
    assert calls["count"] == 2, "the Manager's answer is new evidence for the Planner"
    assert [item.title for item in supervisor.memory.backlog.pending()] == ["Step 2: to_camel"]


def test_work_the_manager_handed_to_an_engineer_is_not_planner_input_until_it_ends(
    tmp_path: Path, monkeypatch
) -> None:
    """2026-10-20: each task the Manager dispatched straight to an Engineer woke
    the Planner as it started, and the Planner only said it was waiting."""
    project = tmp_path / "project"
    project.mkdir()
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    backlog = supervisor.memory.backlog

    def direct(title: str, **fields) -> BacklogItem:
        item = backlog.add(BacklogItem.new(title=title, objective=title, tags=["manager", "manager_direct"]))
        backlog.update(item.id, **fields)
        return item

    choice = direct("Change truncate", park_reason="operator_question", pending_question="A or B?")
    char_count = direct("Add char_count", status="running")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 0, "every live task is in an Engineer's or the operator's hands"

    question = backlog.add(BacklogItem.new(title="Pick a venue", objective="venue"))
    backlog.update(question.id, park_reason="operator_question", pending_question="Which venue?")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1, "the Planner's own question is its input"

    direct("Add word_count", status="running")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1, "another direct task starting is no news to the Planner"

    backlog.update(char_count.id, status="done")
    supervisor.sink.handle_event({
        "type": "life.mission.completed", "item_id": char_count.id, "title": char_count.title,
        "objective": char_count.objective, "success": True, "status": "done",
        "summary": "char_count passes review.",
    })
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2, "a direct task that ended is"
    assert choice.id in {item.id for item in backlog.active()}


def test_a_direct_task_handed_to_the_planner_is_the_planners(
    tmp_path: Path, monkeypatch
) -> None:
    """s-757b9dac: on the Manager's direct task the Engineer found two rounding
    rules and wrote `→ planner`. The task went back to the queue, so the Planner
    read it as still the Engineer's ("every live task is the Manager's direct
    handoff") and the same task ran again, straight into a review of nothing."""
    project = tmp_path / "project"
    project.mkdir()
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    runs: list[str] = []

    class _HandsToPlanner:
        def execute(self, **_kwargs):
            runs.append("engineer")
            return SimpleNamespace(
                success=False, status="replan_requested", rounds=1,
                stop_reason="format_minutes floors, format_days rounds half up; I cannot pick one.",
            )

    supervisor.runner = _HandsToPlanner()
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="Add format_hours", objective="Add format_hours with the package's rounding.",
        tags=["manager", "scope:bounded", "review:required", "manager_direct"],
    ))

    assert supervisor.run()["stopped_by"] == PLAN_AWAITING

    assert runs == ["engineer"], "the task did not run again on its own"
    assert calls["count"] == 1, "the Planner was asked"
    row = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert (row.status, park_reason(row)) == ("parked", "research_open")
    assert planner_may_retire_park(row)
    assert not any(
        "direct handoff" in str(event.get("reason") or "")
        for event in _events(tmp_path / "life") if event.get("type") == "life.planner.idle"
    )


def test_a_planner_left_waiting_on_a_question_hands_the_next_move_to_the_operator(
    tmp_path: Path, monkeypatch
) -> None:
    """2026-10-21: a direct task parked on the operator's choice left the
    Planner saying it waited for unfinished work, not for the operator."""
    project = tmp_path / "project"
    project.mkdir()
    _install_planner(monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    backlog = supervisor.memory.backlog
    item = backlog.add(BacklogItem.new(title="Change truncate", objective="truncate", tags=["manager", "manager_direct"]))
    backlog.update(item.id, park_reason="operator_question", pending_question="A or B?")
    assert supervisor._plan_next_work() == PLAN_AWAITING
    backlog.update(item.id, status="running", pending_question="")
    assert supervisor._plan_next_work() == PLAN_AWAITING

    waits = [event for event in _events(tmp_path / "life") if event["type"] == "life.planner.idle"]
    assert [event["handoff"] for event in waits] == ["operator", ""]


def test_a_manager_steer_wakes_the_planner_only_when_handed_to_it(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.manager.directive import set_active_manager_directive

    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    source = "manager.supervision:steer"

    set_active_manager_directive(life, "Keep one formal review.", source=source, for_planner=False)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1, "a direction for the team is not the Planner's input"

    set_active_manager_directive(life, "Split the benchmark by dataset.", source=source)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 2


def test_an_observer_remark_and_the_steer_it_prompts_leave_the_planner_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """The 2026-10-20 loop: remark, steer, Planner turn, remark, four times over."""
    from argus.core.event_catalog import EventType
    from argus.core.models import RunnerResult
    from argus.daemon.state import write_continuous_config
    from argus.manager import Manager, supervision

    class _Steers:
        def fork(self):
            return self

        def run_exec(self, **_kwargs):
            return RunnerResult(exit_code=0, agent_messages=[
                "ACTION: steer\nREASON: The Planner keeps waking for nothing.\n"
                "EVIDENCE_REFS: backlog.jsonl\nDIRECTIVE: Stop re-planning unchanged work.\n→ planner"
            ])

    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    life.mkdir()
    write_continuous_config(life, enabled=True, objective="keep improving the solver")
    calls = _install_planner(monkeypatch)
    supervisor = _supervisor(project, life)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    remark = {
        "type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "to": "manager",
        "text": "the Planner woke again only to wait", "stall_reason": "observer",
    }
    supervisor.sink.handle_event(remark)
    manager = Manager(life, runner=_Steers(), memory_maintenance_enabled=False)
    assert supervision.supervise(manager, life, remark)["status"] == "applied"

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls["count"] == 1


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
            item.id, park_reason="operator_pause", pending_question="Which dataset should I use?",
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


def test_the_planning_slot_s_open_question_stays_as_asked_beside_other_tasks_questions(
    tmp_path: Path,
) -> None:
    """2026-10-20: every Planner turn reworded its open A-or-B question, each
    vaguer than the last. A question another task waits on is that task's and
    never holds the planning slot's own question back."""
    supervisor = _supervisor(tmp_path / "project", tmp_path / "life")
    backlog = supervisor.memory.backlog
    adopt = backlog.add(BacklogItem.new(title="Adopt reviewed change", objective="deploy"))
    backlog.update(adopt.id, park_reason="operator_question", pending_question="Adopt it?")

    assert supervisor._park_planner_question(None, "Keep A or B?", "conflict") is True
    assert supervisor._park_planner_question(None, "Answer the A/B choice already asked.", "waiting") is False

    asked = {row.title: row.pending_question for row in backlog.active()}
    assert asked == {"Adopt reviewed change": "Adopt it?", "keep improving the solver": "Keep A or B?"}
