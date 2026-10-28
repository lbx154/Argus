"""What follows a Planner turn that adds no work and names who acts next.

A handoff to the Manager is a process question, a task handed back to the
Planner is the Planner's, and a question for the operator waits on the planning
slot's one card. Whatever it answered, the next pass asks the Planner again
(tests/life/test_planner_asked_again_replay.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory, park_reason, planner_may_retire_park
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING
from argus.planner import PlannerVerdict, TaskSpec


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
        summary="the long training run is still producing its result",
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


def test_a_planner_handoff_to_the_manager_is_a_process_question_and_its_diagnosis_wakes_the_planner(
    tmp_path: Path, monkeypatch
) -> None:
    """Step 1 settled, and the Planner handed to the Manager instead of planning
    step 2. That is the Manager's process rung, not a stage decision: no stage
    call is made. Asked again with the Manager's diagnosis, the Planner plans
    step 2 rather than both waiting on each other (2026-10-20: the answer was
    dropped and the project went silent).
    """
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    calls = _install_planner(monkeypatch, [
        PlannerVerdict(
            project_done=False,
            summary="Step 1 passed review; the original task has ended.",
            handoff="manager",
        ),
        PlannerVerdict(
            project_done=False,
            summary="Step 2 is next.",
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
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    idle = [row for row in events if row.get("type") == "life.planner.idle"]
    assert idle and idle[-1]["handoff"] == "manager"
    from argus.manager.directive import set_active_manager_directive

    set_active_manager_directive(
        life, "The environment is fixed; plan step 2 now.", source="manager.supervision:t", for_planner=True,
    )
    supervisor._plan_next_work()

    assert held == [], "the Planner's handoff asks no stage decision"
    assert calls["count"] == 2, "the Planner is asked again with the Manager's diagnosis"
    assert [item.title for item in supervisor.memory.backlog.pending()] == ["Step 2: to_camel"]


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
                success=False, status="planner", rounds=1,
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


def test_a_stage_the_planner_moves_to_without_new_tasks_is_applied(
    tmp_path: Path, monkeypatch
) -> None:
    """s-5dc7ab4a (2026-10-27): Idea passed review and the Planner answered
    advance_to_stage=experiment with no tasks yet, to=engineer. The handoff
    phase returned before the phase that applies a stage request, so the move
    was dropped without a word; the stage stayed Idea, the Planner may only
    plan the current stage, and the project sat 10 h with nothing to run."""
    project = tmp_path / "project"
    project.mkdir()
    _install_planner(monkeypatch, [
        PlannerVerdict(
            project_done=False,
            summary="Idea passed review; measure deployment cost next.",
            handoff="engineer",
            advance_to_stage="experiment",
        ),
    ])
    applied: list[str] = []
    monkeypatch.setattr(
        "argus.life.supervisor._planning_cycle_enqueue._apply_planner_stage_request",
        lambda **kwargs: applied.append(kwargs["requested_stage"]),
    )
    supervisor = _supervisor(project, tmp_path / "life")

    supervisor._plan_next_work()

    assert applied == ["experiment"]


def test_a_planner_waits_on_its_unanswered_question_by_handing_it_on_again(
    tmp_path: Path, monkeypatch
) -> None:
    """s-efe9deac (10-27): waiting on its own unanswered spot-check question,
    the Planner tried to=operator without ask, was refused 46 times ("to=operator
    goes with ask"), and handed to the Manager instead "to avoid asking twice",
    starting supervision rounds. The plan form says how to wait, and waiting
    that way leaves the one question as it was first asked."""
    from argus.core.handoff import form_tool

    assert "while your question to them is unanswered, hand to=operator with it again" in (
        form_tool("plan")["description"]
    )
    project = tmp_path / "project"
    project.mkdir()
    asked = PlannerVerdict(
        project_done=False, summary="Spot-check cases 1, 7 and 22 first.", handoff="operator",
        operator_question="Do you agree with the three spot-check verdicts?",
    )
    again = replace(asked, summary="Still waiting on the spot-check answer.")
    _install_planner(monkeypatch, [asked, again])
    supervisor = _supervisor(project, tmp_path / "life")

    supervisor._plan_next_work()
    supervisor._plan_next_work()

    questions = [row.pending_question for row in supervisor.memory.backlog.active() if row.pending_question]
    assert questions == ["Do you agree with the three spot-check verdicts?"]


def test_a_revision_s_question_still_waiting_stays_as_first_asked(tmp_path: Path) -> None:
    """The plan form tells the Planner that asking again while its question
    waits leaves that question as first asked; that holds for a question the
    Planner put on the task whose revision it is writing, too."""
    supervisor = _supervisor(tmp_path / "project", tmp_path / "life")
    backlog = supervisor.memory.backlog
    source = backlog.add(BacklogItem.new(title="Paired-credit arm", objective="run the arm"))
    revising = SimpleNamespace(revision_request={"item_id": source.id})

    assert supervisor._park_planner_question(revising, "Repair the shared runner first?", "blocked") is True
    assert supervisor._park_planner_question(revising, "Still waiting: repair it first?", "blocked") is False

    row = next(row for row in backlog.active() if row.id == source.id)
    assert row.pending_question == "Repair the shared runner first?"
