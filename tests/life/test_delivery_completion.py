from __future__ import annotations

import json

from argus.core.launch_settings import launch_note
from argus.core.planner_verdict import PlannerVerdictStatus
from argus.core.transcript import read_turns
from argus.life.event_log import JsonlEventSink
from argus.life.memory import LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from tests.manager_inbox import team_reports


class _Manager:
    def bind_execution_workdir(self, _workdir):
        return self


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
            "source": "reviewer_accepted",
            "why": "Declared by the Engineer and accepted by the Reviewer.",
        },
        "targets": [{
            "path": "results/final.md",
            "label": "final.md",
            "source": "reviewer_accepted",
            "why": "Declared by the Engineer and accepted by the Reviewer.",
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

    transcript = team_reports(tmp_path)
    assert transcript[-1]["delivery"] == delivery
    assert transcript[-1]["delivery_id"] == delivery["delivery_id"]
    assert "The result is in results/final.md." in transcript[-1]["text"]
    # The chat shows the team's message at once; no Manager turn waits on it.
    [shown] = [turn for turn in read_turns(tmp_path) if turn.get("delivery_id")]
    assert shown["message_id"] == transcript[-1]["message_id"]
    assert shown["text"] == transcript[-1]["text"] and shown["delivery"] == delivery
    assert '"type":"life.manager.inbox"' not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert not (tmp_path / "manager-deferred").exists()

    # s-fcf4b146 (10-24): the project's completion then showed the same card
    # again. It is already in the chat, so nothing more is said.
    supervisor._manager_publish_project_report("All planned work is done.", delivery=delivery)
    assert [turn["message_id"] for turn in read_turns(tmp_path)] == [shown["message_id"]]


def test_a_finished_one_task_project_is_in_the_chat_when_the_task_completes(tmp_path) -> None:
    """s-ec08e4a5 (10-24): the task's message was held for the project report,
    written after learning, reflection and the Planner (57 s), which the
    Manager then answered: 4.7 minutes in all."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = LifeMemory.open(tmp_path / "state")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective="Write the report", open_ended=False,
            project_worktree=workspace,
        ),
    )
    delivery = {
        "delivery_id": "delivery:task-1:task_completed:1", "kind": "task_completed", "item_id": "task-1",
        "summary": "Wrote and reviewed the report.", "status": "done", "review_status": "done",
        "primary_target": {"path": "report.md", "label": "report.md"},
    }
    completed = {
        "type": "life.mission.completed", "item_id": "task-1", "title": "Write the report",
        "success": True, "status": "done", "summary": "Wrote and reviewed the report.",
        "campaign_continues": False, "overall_complete": True,
        "mission_delivery_id": "md-1", "outcome": {"review_status": "done"},
        "delivery": delivery, "delivery_id": delivery["delivery_id"],
    }
    # The completion's durable receipt is acknowledged once the chat shows it.
    assert supervisor._confirm_mission_completion_receipt(completed)
    assert supervisor._confirm_mission_completion_receipt(completed)

    [turn] = read_turns(memory.root)
    assert turn["message_id"] == "mission-result-md-1"
    assert "Wrote and reviewed the report." in turn["text"]
    assert turn["delivery"] == delivery
    assert not (memory.root / "manager-deferred").exists()


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
    turns = team_reports(memory.root)
    assert "One step done" in turns[-1]["text"]
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

    turns = team_reports(memory.root)
    # One report tells the Manager the project is done, carrying the project's
    # delivery card; the Manager writes the operator's message in its session.
    completion_turn = next(turn for turn in reversed(turns) if turn.get("delivery"))
    assert completion_turn["text"]
    assert completion_turn["delivery"]["primary_target"]["path"] == "final.md"
    assert not any(str(turn.get("message_id", "")).endswith("-project-completed") for turn in turns)


def _delivery_supervisor(tmp_path) -> tuple[LifeSupervisor, LifeMemory]:
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
    supervisor._manager_publish_project_report = lambda _reason, **_kwargs: "reported"
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
    return supervisor, memory


def test_project_delivery_is_titled_by_the_request_without_its_launch_settings(tmp_path) -> None:
    # Trial s-757b9dac: the completion card's title went on with
    # "【开工设置（我在界面上选的）】 - 成果形式：可以运行的代码和使用说明。".
    supervisor, _memory = _delivery_supervisor(tmp_path)
    request = "给 timefmt 加一个 format_hours(seconds)"
    supervisor.config.continuous_objective = request + launch_note({"deliverable": "code"}, language_hint=request)
    delivery = supervisor._build_terminal_project_delivery("All planned work is done.")
    assert delivery is not None
    assert delivery["title"] == request


def test_terminal_delivery_picks_the_success_over_later_non_success_settlements(
    tmp_path,
) -> None:
    """Later failed/paused settlements never displace the successful one.

    ``success`` must also be a literal ``True``: the journal's kind projection
    treats a MISSING ``success`` as complete, and such a row must not be
    promoted into a delivery receipt.
    """
    supervisor, memory = _delivery_supervisor(tmp_path)
    with (memory.root / "events.jsonl").open("a", encoding="utf-8") as fh:
        for row in (
            {
                "type": "life.mission.completed",
                "item_id": "stage-2",
                "ts": 1_000.0,
                "success": False,
                "status": "failed",
                "title": "Follow-up attempt",
                "summary": "regressed",
            },
            {
                "type": "life.mission.completed",
                "item_id": "stage-3",
                "ts": 1_001.0,
                "success": False,
                "status": "paused_budget",
                "title": "Budget pause",
                "summary": "cap reached",
            },
            {
                # No ``success`` field at all: kind projection defaults it to
                # complete, but the receipt requires the literal True.
                "type": "life.mission.completed",
                "item_id": "stage-4",
                "ts": 1_002.0,
                "status": "done",
                "title": "Ambiguous settlement",
                "summary": "no explicit success flag",
            },
        ):
            fh.write(json.dumps(row) + "\n")

    delivery = supervisor._build_terminal_project_delivery("All planned work is done.")

    assert delivery is not None
    assert delivery["primary_target"]["path"] == "final.md"
    assert delivery["summary"] == "The final stage produced a reviewed file."


def test_terminal_delivery_collects_the_files_declared_for_its_own_goal(tmp_path):
    from pathlib import Path

    from argus.life.memory import BacklogItem
    supervisor, memory = _delivery_supervisor(tmp_path)
    workspace = str(supervisor._project_workdir())
    root = Path(workspace)
    (root / 'index.html').write_text('<h1>Reviewed website</h1>')
    (root / 'REPORT.md').write_text('# Reviewed report')
    (root / 'unrelated.md').write_text('Old goal')
    goal = supervisor.config.continuous_objective
    for item_id, original, declared in [
        ('old-goal', 'Different goal', ['unrelated.md']),
        ('website', goal, ['index.html']),
        ('review', goal, ['REPORT.md']),
    ]:
        item = BacklogItem.new(item_id=item_id, title=item_id, objective=original)
        item.original_objective = original
        item.status = "done"
        memory.backlog.add(item)
        assert supervisor._emit({
            'type': 'life.mission.completed', 'item_id': item_id,
            'success': True, 'status': 'done', 'overall_complete': False,
            'campaign_continues': True, 'summary': '', 'final_output': 'Delivered `unrelated.md`.',
            'execution_workdir': workspace, 'delivery_candidates': declared, 'review_source': 'reviewer',
            'outcome': {'review_status': 'done'},
        })
    receipt = supervisor._build_terminal_project_delivery('Certified complete')
    assert receipt is not None
    assert {target['path']: target['source'] for target in receipt['targets']} == {
        'REPORT.md': 'reviewer_accepted', 'index.html': 'reviewer_accepted',
    }


def test_software_delivery_retains_the_reviewed_product_ahead_of_source_files(tmp_path, monkeypatch):
    from pathlib import Path

    from argus.life.memory import BacklogItem
    from argus.skills import vertical_select
    supervisor, memory = _delivery_supervisor(tmp_path)
    root = Path(supervisor._project_workdir())
    (root / "index.html").write_text("<h1>Product</h1>")
    (root / "REPORT.md").write_text("Run `node verify.js` to reproduce.")
    sources = [f"module{i}.js" for i in range(8)]
    for name in sources:
        (root / name).write_text("// reviewed source")
    item = BacklogItem.new(item_id="ui", title="Build product", objective="Create index.html and REPORT.md.")
    item.original_objective = supervisor.config.continuous_objective
    item.status = "done"
    memory.backlog.add(item)
    assert supervisor._emit({
        "type": "life.mission.completed", "item_id": item.id, "success": True,
        "status": "done", "overall_complete": False, "campaign_continues": True,
        "execution_workdir": str(root), "delivery_candidates": [*sources, "REPORT.md", "index.html"],
        "final_output": "All controls passed.", "outcome": {"review_status": "done"},
    })
    monkeypatch.setattr(vertical_select, "resolve_vertical_if_decided", lambda _: "software")
    receipt = supervisor._build_terminal_project_delivery("Verified")
    assert [row["path"] for row in receipt["targets"]][:2] == ["index.html", "REPORT.md"]


def test_a_reviewed_task_is_announced_once_in_the_reviewer_s_words(tmp_path) -> None:
    """Trial s-cc7ba48e 1c: the completion message quoted the Engineer's handoff
    ("still awaiting the independent rerun") under "checked by an independent
    reviewer", and the project receipt named another primary file."""
    from dataclasses import dataclass

    from argus.life.memory import BacklogItem
    from argus.life.supervisor import LifeBudget

    @dataclass
    class _Outcome:
        success: bool = True
        status: str = "done"
        stop_reason: str = ""
        stop_kind: str | None = None
        recoverable: bool = False
        rounds: int = 1
        final_review_status: str = "done"
        final_review_source: str = "reviewer"
        final_review_reason: str = "Review passed: reran the full suite, 138 passed."
        final_residual_risk: str = ""
        final_message: str = ""
        summary: str = "Implemented wrap in `core.py`; awaiting the independent rerun."
        final_output: str = "Changed `core.py` and `README.md`."
        deliverables: tuple = ("core.py", "README.md")

    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"), runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=tmp_path / "life", verbosity="full"),
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "core.py").write_text("def wrap(): ...\n", encoding="utf-8")
    (workdir / "README.md").write_text("# wrap\n", encoding="utf-8")
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Add wrap", objective="Add wrap to core.py",
        tags=["manager_direct", "scope:bounded", "review:required"],
    ))
    supervisor.runner.execute = lambda **kwargs: _Outcome()
    supervisor.tick()

    life = tmp_path / "life"
    events = [json.loads(line) for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    completed = next(event for event in events if event.get("type") == "life.mission.completed")
    assert completed["summary"] == "Review passed: reran the full suite, 138 passed."
    assert completed["delivery"]["summary"] == completed["summary"]
    messages = [report["text"] for report in team_reports(life) if report.get("mission_result")]
    assert len(messages) == 1
    assert "138 passed" in messages[0] and "awaiting" not in messages[0]
    project = supervisor._build_terminal_project_delivery("done")
    assert project["delivery_id"] == completed["delivery"]["delivery_id"]
    assert project["primary_target"] == completed["delivery"]["primary_target"]
    # The Manager's project report reads that same verdict instead of
    # searching for a REVIEW.md that a direct task never writes.
    assert supervisor._manager_project_completion_context()["final_delivery_review"] == {
        "review_source": "reviewer", "review_status": "done", "summary": completed["summary"],
    }
