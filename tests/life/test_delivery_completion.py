from __future__ import annotations

import json

from argus_skill.core.planner_verdict import PlannerVerdictStatus
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Manager:
    def bind_execution_workdir(self, _workdir):
        return self

    def report_project_completion(self, **_kwargs):
        return "Manager reviewed the full stage ledger and confirmed completion."


class _Runner:
    manager = _Manager()


def test_completion_message_carries_one_structured_delivery_receipt(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=False, open_ended=False),
    )
    delivery = {
        "schema_version": 1,
        "delivery_id": "delivery:task-1:task_completed",
        "kind": "task_completed",
        "item_id": "task-1",
        "title": "Create final report",
        "summary": "Wrote and reviewed the final report.",
        "status": "done",
        "review_status": "done",
        "delivered_at": 1.0,
        "primary_target": {
            "path": "results/final.md",
            "label": "final.md",
            "source": "reviewer_evidence",
            "why": "Reviewed evidence.",
        },
        "targets": [{
            "path": "results/final.md",
            "label": "final.md",
            "source": "reviewer_evidence",
            "why": "Reviewed evidence.",
        }],
    }

    assert supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "task-1",
        "title": "Create final report",
        "success": True,
        "status": "done",
        "summary": "Wrote and reviewed the final report.",
        "outcome": {"review_status": "done"},
        "delivery": delivery,
        "delivery_id": delivery["delivery_id"],
    })

    transcript = [
        json.loads(line)
        for line in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert transcript[-1]["delivery"] == delivery
    assert transcript[-1]["delivery_id"] == delivery["delivery_id"]
    ui_events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if '"type":"ui.argus"' in line
    ]
    assert len(ui_events) == 1
    assert ui_events[0]["delivery"] == delivery


def test_continuous_mission_only_delivers_after_project_done(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "final.md").write_text("# Final\n", encoding="utf-8")
    memory = LifeMemory.open(tmp_path / "state")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="Finish the restored task",
            open_ended=False,
            project_worktree=workspace,
        ),
    )
    supervisor._manager_publish_project_report = lambda _reason: "reported"

    assert supervisor._emit({
        "type": "life.mission.completed",
        "item_id": "stage-1",
        "title": "Resume the remaining stage",
        "success": True,
        "status": "done",
        "summary": "The final stage produced a reviewed file.",
        "campaign_continues": True,
        "overall_complete": False,
        "execution_workdir": str(workspace),
        "delivery_candidates": ["final.md"],
        "outcome": {
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
        "delivery": None,
        "delivery_id": "",
    })
    turns = [
        json.loads(line)
        for line in (memory.root / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "Task continued" in turns[-1]["text"]
    assert turns[-1].get("delivery") is None

    delivery = supervisor._build_terminal_project_delivery("All planned work is done.")
    assert delivery is not None
    assert delivery["primary_target"]["path"] == "final.md"
    assert supervisor._emit_planner_verdict(
        status=PlannerVerdictStatus.COMPLETED,
        reason="All planned work is done.",
        completion_kind="project_completed",
        resume_outcome=False,
        project_done=True,
        delivery=delivery,
    )

    turns = [
        json.loads(line)
        for line in (memory.root / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    completion_turn = next(turn for turn in reversed(turns) if turn.get("delivery"))
    assert "Task completed" in completion_turn["text"]
    assert completion_turn["delivery"]["primary_target"]["path"] == "final.md"
