"""Replay of s-efe9deac: with nothing running, every pass asks the Planner again.

10-27 06:14:29 the Planner, asked after a task passed review, added nothing
and left the engine-repair question with the operator. Nothing ran, and the
unchanged-input skip kept it silent for all 14 passes until the operator
answered at 07:10:16. 10-26 06:30:21 it handed two trainings that never got a
GPU to the Manager; the skip kept it silent for 15 hours. A Manager answer for
the team, such as d57a69c9 of 10-27 00:04, which asked the Planner to schedule
the next step, moves nothing the Planner reads, so the skip held it until the
operator wrote. The fixture keeps the recorded answers, tasks and questions.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.daemon.state import write_continuous_config
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING
from argus.planner import PlannerVerdict, TaskSpec

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "planner_asked_again.json")
    .read_text(encoding="utf-8")
)


def _team(root: Path, monkeypatch, verdicts: list[PlannerVerdict]) -> tuple[LifeSupervisor, list[PlannerVerdict]]:
    asked: list[PlannerVerdict] = []

    def plan_next(_planner, **_kwargs):
        asked.append(verdicts.pop(0))
        return asked[-1]

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan_next)
    life, project = root / "life", root / "project"
    project.mkdir(parents=True)
    write_continuous_config(life, enabled=True, objective=FIXTURE["objective"])
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(life), runner=object(), planner_runner=object(),
        sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective=FIXTURE["objective"], final_certification_gate=False,
            project_worktree=project, artifact_root=project,
        ),
    )
    supervisor._vertical_resolved = True
    return supervisor, asked


def _park(supervisor: LifeSupervisor, row: dict, **fields) -> None:
    backlog = supervisor.memory.backlog
    item = backlog.add(BacklogItem.new(title=row["title"], objective=row["title"]))
    backlog.update(item.id, **fields)


def _events(supervisor: LifeSupervisor) -> list[dict]:
    path = supervisor.memory.root / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_pass_after_a_no_new_work_answer_asks_the_planner_again(tmp_path, monkeypatch):
    """06:14:29, then the pass at 06:15:06: nothing runs, the question waits."""
    replay = FIXTURE["idle_0614"]
    answer = PlannerVerdict(project_done=False, **replay["verdict"])
    supervisor, asked = _team(tmp_path, monkeypatch, [answer, answer])
    asked_row = replay["asked"]
    _park(supervisor, asked_row, park_reason="operator_question", pending_question=asked_row["question"])
    supervisor.sink.handle_event({
        "type": "life.mission.completed", "success": True, "status": "done", **replay["completed"],
    })

    first = supervisor.run()
    second = supervisor.run()

    assert len(asked) == 2, "the next pass asks the Planner, not its last answer"
    # Paced by the idle backoff the daemon sleeps between passes.
    assert [(run["stopped_by"], run["suggested_sleep"]) for run in (first, second)] == [
        (PLAN_AWAITING, 15.0), (PLAN_AWAITING, 30.0),
    ]
    assert not [event for event in _events(supervisor) if event.get("model_call_skipped")]


def test_a_manager_answer_for_the_team_leaves_the_planner_asked_again(tmp_path, monkeypatch):
    """06:30:21 the Planner hands the GPU-less trainings to the Manager, who answers to=team."""
    from argus.core.event_catalog import EventType
    from argus.core.models import RunnerResult
    from argus.manager import Manager, supervision
    from argus.manager.directive import load_active_manager_directive
    from tests.hand_off import hand_off, submit

    replay = FIXTURE["handoff_0630"]
    planned = replay["next_verdict"]
    supervisor, asked = _team(tmp_path, monkeypatch, [
        PlannerVerdict(project_done=False, **replay["verdict"]),
        PlannerVerdict(
            project_done=False, summary=planned["summary"],
            new_tasks=[TaskSpec(**task) for task in planned["tasks"]],
        ),
    ])
    for row in replay["to_manager"]:
        _park(supervisor, row, park_reason="")
    _park(supervisor, replay["asked"], park_reason="operator_question", pending_question=replay["asked"]["question"])

    assert supervisor._plan_next_work() == PLAN_AWAITING
    handoff = next(event for event in _events(supervisor) if event["type"] == EventType.LIFE_PLANNER_IDLE)
    assert handoff["handoff"] == "manager"

    team = replay["manager_answer"]

    class _ForTheTeam:
        def fork(self):
            return self

        def run_exec(self, *, options, **_kwargs):
            answer = hand_off("team", summary=team["reason"], note=team["directive"], evidence_refs=["backlog.jsonl"])
            assert not submit(options, answer)
            return RunnerResult(exit_code=0)

    life = supervisor.memory.root
    trigger = {**handoff, "process_stuck": True, "consult_reason": f"the Planner says the process is stuck: {handoff['reason']}"}
    assert supervision.supervise(Manager(life, runner=_ForTheTeam()), life, trigger)["status"] == "applied"
    assert load_active_manager_directive(life).planner_revision == "", "an answer for the team"

    assert supervisor._plan_next_work() is True
    assert len(asked) == 2
    assert [item.title for item in supervisor.memory.backlog.pending()] == [task["title"] for task in planned["tasks"]]
    kinds = {event["type"] for event in _events(supervisor)}
    assert not kinds & {EventType.LIFE_PLANNER_TERMINAL_IDLE, EventType.LIFE_PLANNER_ERROR}
