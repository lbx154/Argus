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
    assert "Deliverable: results/final.md" in transcript[-1]["text"]
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
    return supervisor, memory


def test_terminal_delivery_survives_journal_noise_after_the_settlement(tmp_path) -> None:
    """Journal chatter after the winning settlement must not hide it.

    The receipt used to read ``journal.tail(80)``: 90 waiting heartbeats after
    the settlement evicted it from the window and the terminal delivery lost
    its verified output.
    """
    supervisor, memory = _delivery_supervisor(tmp_path)
    with (memory.root / "events.jsonl").open("a", encoding="utf-8") as fh:
        for index in range(90):
            fh.write(json.dumps({
                "type": "life.planner.waiting",
                "ts": 1_000.0 + index,
                "reason": "waiting on external dependency",
            }) + "\n")

    delivery = supervisor._build_terminal_project_delivery("All planned work is done.")

    assert delivery is not None
    assert delivery["primary_target"]["path"] == "final.md"
    assert delivery["summary"] == "The final stage produced a reviewed file."


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


def test_terminal_delivery_recovers_accepted_handoff_files_for_its_own_goal(tmp_path):
    from argus_skill.life.memory import BacklogItem
    supervisor, memory = _delivery_supervisor(tmp_path)
    workspace = str(supervisor._project_workdir())
    from pathlib import Path
    root = Path(workspace)
    (root / 'index.html').write_text('<h1>Reviewed website</h1>')
    (root / 'REPORT.md').write_text('# Reviewed report')
    (root / 'unrelated.md').write_text('Old goal')
    goal = supervisor.config.continuous_objective
    for item_id, original, output in [
        ('old-goal', 'Different goal', 'Delivered `unrelated.md`.'),
        ('website', goal, 'Delivered `index.html`.'),
        ('review', goal, 'RESULT=Validated `REPORT.md`.'),
    ]:
        item = BacklogItem.new(item_id=item_id, title=item_id, objective=original)
        item.original_objective = original
        item.status = "done"
        memory.backlog.add(item)
        assert supervisor._emit({
            'type': 'life.mission.completed', 'item_id': item_id,
            'success': True, 'status': 'done', 'overall_complete': False,
            'campaign_continues': True, 'summary': '', 'final_output': output,
            'execution_workdir': workspace, 'delivery_candidates': [],
            'outcome': {'review_status': 'done'},
        })
    receipt = supervisor._build_terminal_project_delivery('Certified complete')
    assert receipt is not None
    paths = {target['path'] for target in receipt['targets']}
    assert paths == {'REPORT.md', 'index.html'}
    assert 'unrelated.md' not in paths


def test_software_completion_context_includes_delivered_files_and_vertical_certificate(tmp_path, monkeypatch):
    supervisor, memory = _delivery_supervisor(tmp_path)
    monkeypatch.setattr(supervisor, '_effective_final_certification_gate', lambda _root: False)
    monkeypatch.setattr(supervisor, '_manager_final_stage_is_completed', lambda: True)
    monkeypatch.setattr(supervisor, '_journal_has_final_certification', lambda: False)
    context = supervisor._manager_project_completion_context()
    assert context['current_final_certification']['certified'] is True
    assert context['current_final_certification']['scope'] == 'vertical_completion'
    assert 'final.md' in {row['path'] for row in context['current_artifact_evidence']}


def test_plain_completion_filenames_and_report_links_remain_confined(tmp_path):
    from argus_skill.life.delivery import linked_report_paths, referenced_delivery_paths
    (tmp_path / "REPORT.md").write_text("Reviewed website: `index.html`; reproduce with `node validate.js`.\n")
    (tmp_path / "index.html").write_text("<h1>Reviewed</h1>")
    (tmp_path / "validate.js").write_text("console.log('ok')")
    (tmp_path / ".env").write_text("secret")
    paths = referenced_delivery_paths(tmp_path, ["RESULT=REPORT.md is ready. https://example.com/index.html is external."])
    assert paths == ["REPORT.md"]
    assert linked_report_paths(tmp_path, paths) == ["index.html", "validate.js"]
    assert referenced_delivery_paths(tmp_path, ["../private.md and .env are not deliverables."]) == []


def test_spreadsheet_completion_is_deliverable_without_markdown_link(tmp_path):
    from argus_skill.life.delivery import referenced_delivery_paths

    (tmp_path / "budget.xlsx").write_bytes(b"spreadsheet fixture")
    assert referenced_delivery_paths(tmp_path, ["Created budget.xlsx."]) == ["budget.xlsx"]
    assert referenced_delivery_paths(tmp_path, ["Created `budget.xlsx`."]) == ["budget.xlsx"]
    assert referenced_delivery_paths(tmp_path, ["../budget.xlsx"]) == []


def test_software_delivery_retains_the_reviewed_product_ahead_of_source_files(tmp_path, monkeypatch):
    from pathlib import Path

    from argus_skill.life.memory import BacklogItem
    from argus_skill.skills import vertical_select
    supervisor, memory = _delivery_supervisor(tmp_path)
    root = Path(supervisor._project_workdir())
    (root / "index.html").write_text("<h1>Product</h1>")
    (root / "REPORT.md").write_text("Run `node verify.js` to reproduce.")
    sources = [f"module{i}.js" for i in range(8)] + ["verify.js"]
    for name in sources:
        (root / name).write_text("// reviewed source")
    item = BacklogItem.new(item_id="ui", title="Build product", objective="Create index.html and REPORT.md.")
    item.original_objective = supervisor.config.continuous_objective
    item.status = "done"
    memory.backlog.add(item)
    assert supervisor._emit({
        "type": "life.mission.completed", "item_id": item.id, "success": True,
        "status": "done", "overall_complete": False, "campaign_continues": True,
        "execution_workdir": str(root), "delivery_candidates": sources,
        "final_output": "All controls passed.", "outcome": {"review_status": "done"},
    })
    monkeypatch.setattr(vertical_select, "resolve_vertical_if_decided", lambda _: "software")
    receipt = supervisor._build_terminal_project_delivery("Verified")
    assert receipt["primary_target"]["path"] == "index.html"
    paths = [row["path"] for row in receipt["targets"]]
    assert "REPORT.md" in paths and "verify.js" in paths


def test_terminal_delivery_identity_changes_for_new_completed_work_in_one_session(tmp_path):
    supervisor, memory = _delivery_supervisor(tmp_path)
    first = supervisor._build_terminal_project_delivery("First goal done")
    assert supervisor._emit({
        "type": "life.mission.completed", "item_id": "second-goal",
        "success": True, "status": "done", "summary": "Second goal done",
        "overall_complete": False, "campaign_continues": True,
        "execution_workdir": str(supervisor._project_workdir()),
        "delivery_candidates": ["final.md"], "outcome": {"review_status": "done"},
    })
    second = supervisor._build_terminal_project_delivery("Second goal done")
    replay = supervisor._build_terminal_project_delivery("Second goal done")
    assert first["delivery_id"] != second["delivery_id"]
    assert second["delivery_id"] == replay["delivery_id"]


def test_terminal_delivery_recovers_reviewed_product_from_an_older_direct_settlement(tmp_path):
    supervisor, memory = _delivery_supervisor(tmp_path)
    workspace = supervisor._project_workdir()
    (workspace / "index.html").write_text("<h1>Travel planner</h1>", encoding="utf-8")
    item_id = "direct-website"
    events = [
        {"type": "life.mission.started"},
        {
            "type": "engineer.progress", "kind": "tool_use", "agent_layer": "engineer",
            "tool_name": "apply_patch", "text": "apply_patch: *** Begin Patch\n*** Add File: index.html\n+product",
        },
        {"type": "round.review.started"},
        {
            "type": "engineer.progress", "kind": "tool_use", "agent_layer": "reviewer",
            "tool_name": "view", "text": 'view: {"path": "index.html"}',
        },
        {"type": "round.review.completed", "status": "done", "review_source": "reviewer"},
    ]
    with (memory.root / "events.jsonl").open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps({"item_id": item_id, **event}) + "\n")
    assert supervisor._emit({
        "type": "life.mission.completed", "item_id": item_id,
        "success": True, "status": "done", "overall_complete": True,
        "summary": "Browser checks passed.", "final_output": "RESULT=The travel planner is complete.",
        "execution_workdir": str(workspace), "delivery_candidates": [],
        "outcome": {"review_status": "done"}, "delivery": None,
    })

    receipt = supervisor._build_terminal_project_delivery("Complete")

    assert receipt["primary_target"]["path"] == "index.html"
    assert receipt["delivery_id"] == f"delivery:project-{memory.root.name}-{item_id}:task_completed"
