from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.core.mission_view import (
    load_mission_view,
    snapshot_mission_view,
    update_mission_view_event,
)


def emit(root: Path, event_type: str, ts: float, **payload) -> dict:
    return update_mission_view_event(root, {"type": event_type, "ts": ts, **payload})


def test_legacy_team_waiting_projects_as_planner_waiting(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.team.waiting",
        1,
        reason="await external worker",
    )

    planner = next(role for role in view["roles"] if role["role"] == "planner")
    assert planner["status"] == "waiting"
    assert planner["kind"] == "planner_waiting"
    assert planner["label"] == (
        "Waiting for work outside Argus to finish before planning further"
    )
    assert view["timeline"][-1]["kind"] == "planner_waiting"
    assert view["timeline"][-1]["title"] == planner["label"]


def test_manager_handoff_refreshes_stage_after_objective_update(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.manager.stage_decision",
        1,
        action="advance",
        target_stage="run",
    )
    view = emit(
        tmp_path,
        "life.manager.intent.completed",
        2,
        intent_id="intent-updated",
        objective="Extended standing objective",
        vertical="research",
        stages=["research", "plan", "benchmark", "run"],
        current_stage="research",
    )

    assert view["stage"] == {"id": "research", "label": "Research"}


def test_manager_grounding_lifecycle_is_visible(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.manager.intent.started",
        1,
        item_id="instance-owner__repo-abc",
        objective="Repair parser behavior",
    )

    roles = {role["role"]: role for role in view["roles"]}
    assert view["mission"]["status"] == "grounding"
    assert view["mission"]["objective"] == "Repair parser behavior"
    assert view["active_role"] == "manager"
    assert roles["manager"]["kind"] == "grounding_started"
    assert roles["manager"]["label"] == "Reading the request to work out what it asks for"

    view = emit(
        tmp_path,
        "life.manager.intent.completed",
        2,
        item_id="instance-owner__repo-abc",
        objective="Repair parser behavior",
        execution_task="Repair parser behavior\n\nManager grounding",
        vertical="software",
        workflow_mode="staged",
        route="team",
        lifetime="bounded",
        continuous=True,
        open_ended=False,
        reason="grounded",
    )
    roles = {role["role"]: role for role in view["roles"]}
    assert view["mission"]["status"] == "framed"
    assert view["routing"] == {
        "route": "team",
        "vertical": "software",
        "workflow_mode": "staged",
        "lifetime": "bounded",
        "continuous": True,
        "open_ended": False,
    }
    assert roles["manager"]["status"] == "done"
    view = emit(
        tmp_path,
        "life.planner.task_added",
        3,
        item_id="task-1",
        title="Fix the CLI",
    )
    planner = next(role for role in view["roles"] if role["role"] == "planner")
    assert planner["kind"] == "task_added"
    assert planner["label"] == "A new task was added to the plan."


def test_manager_intent_failure_is_not_labeled_as_grounding_failed(
    tmp_path: Path,
) -> None:
    emit(
        tmp_path,
        "life.manager.intent.started",
        1,
        item_id="task-1",
        objective="Route this task",
    )

    view = emit(
        tmp_path,
        "life.manager.intent.failed",
        2,
        item_id="task-1",
        objective="Route this task",
        error="VerticalDecisionError: no runnable vertical",
    )

    roles = {role["role"]: role for role in view["roles"]}
    assert view["mission"]["status"] == "failed"
    assert roles["manager"]["status"] == "error"
    assert roles["manager"]["label"] == "I couldn't determine how to handle this request."
    assert view["timeline"][-1]["title"] == roles["manager"]["label"]
    assert view["role_work"][-1]["title"] == roles["manager"]["label"]
    assert "VerticalDecisionError" not in view["timeline"][-1]["detail"]
    assert "VerticalDecisionError" not in view["role_work"][-1]["detail"]


def test_manager_stage_decision_uses_human_action_and_status(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.manager.stage_decision",
        1,
        action="rollback",
        target_stage="paper_review",
        reason="The submission evidence is stale.",
    )

    manager = next(role for role in view["roles"] if role["role"] == "manager")
    assert manager["kind"] == "stage_rolled_back"
    assert manager["label"] == "The project went back to the paper review stage."
    assert view["timeline"][-1]["title"] == "The project went back to the paper review stage."
    assert view["role_work"][-1]["status"] == "done"
    assert view["role_work"][-1]["detail"] == "The submission evidence is stale."


def test_load_normalizes_persisted_legacy_manager_failure_label(
    tmp_path: Path,
) -> None:
    (tmp_path / "mission-view.json").write_text(
        '{"schema_version":3,"bootstrapped":true,'
        '"roles":[{"role":"manager","status":"error","label":"Grounding failed"}],'
        '"timeline":[{"type":"life.manager.intent.failed","title":"Grounding failed"}],'
        '"role_work":[{"role":"manager","status":"error","title":"Grounding failed"}]}',
        encoding="utf-8",
    )

    view = load_mission_view(tmp_path)

    assert view["roles"][0]["label"] == "Manager routing failed"
    assert view["timeline"][0]["title"] == "Manager routing failed"
    assert view["role_work"][0]["title"] == "Manager routing failed"


def test_v3_snapshot_rebuilds_to_include_completion_summary(tmp_path: Path) -> None:
    (tmp_path / "mission-view.json").write_text(
        '{"schema_version":3,"bootstrapped":true,"mission":{"status":"complete"}}',
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps({
            "type": "life.mission.completed",
            "ts": 1,
            "item_id": "task-summary",
            "title": "Create result",
            "status": "done",
            "success": True,
            "summary": "Created RESULT.txt and verified its exact contents.",
        })
        + "\n",
        encoding="utf-8",
    )

    view = snapshot_mission_view(
        tmp_path,
        session={},
        daemon={},
        roles=[],
        backlog=[],
    )

    assert view["schema_version"] == 7
    assert view["mission"]["summary"] == (
        "Created RESULT.txt and verified its exact contents."
    )


def test_mission_view_preserves_final_engineer_output_beyond_summary(
    tmp_path: Path,
) -> None:
    superseded_draft = "# Superseded draft\n\n" + ("obsolete detail\n" * 400)
    body = "# Complete report\n\n" + ("final detail\n" * 100)
    emit(
        tmp_path,
        "life.mission.started",
        1,
        item_id="task-long",
        title="Write report",
        objective="Deliver the complete report",
    )
    emit(
        tmp_path,
        "engineer.progress",
        2,
        agent_layer="engineer",
        kind="agent_message",
        text=superseded_draft,
    )
    emit(
        tmp_path,
        "engineer.progress",
        3,
        agent_layer="engineer",
        kind="agent_message",
        final_delivery=True,
        text=f"{body}\nMILESTONE_STATUS=done\nOPERATOR_QUESTION=none",
    )
    view = emit(
        tmp_path,
        "life.mission.completed",
        4,
        item_id="task-long",
        title="Write report",
        status="done",
        success=True,
        summary=body[:1200],
    )

    assert view["mission"]["summary"] == body[:1200].strip()
    assert view["mission"]["final_output"] == body.strip()


@pytest.mark.parametrize("authoritative", [None, "", "# Accepted\n\n" + "detail\n" * 300])
def test_final_output_uses_last_delivery_not_later_progress(
    tmp_path: Path, authoritative: str | None,
) -> None:
    emit(tmp_path, "life.mission.started", 1, item_id="task")
    for ts, final_delivery, text, role in [
        (2, True, "Superseded handoff\n" * 500, "engineer"),
        (3, True, "# Last handoff\n\nkept\nDecision:\nNEXT_OWNER=reviewer", "main"),
        (4, False, "Later progress, not a delivery", "engineer"),
        (5, True, "Reviewer response, not Engineer output", "reviewer"),
    ]:
        emit(
            tmp_path, "engineer.progress", ts,
            agent_layer=role, kind="agent_message",
            final_delivery=final_delivery, text=text,
        )
    payload = {} if authoritative is None else {"final_output": authoritative}
    view = emit(
        tmp_path, "life.mission.completed", 6, item_id="task",
        status="done", success=True, summary="Compact summary", **payload,
    )
    expected = "# Last handoff\n\nkept" if authoritative is None else authoritative.strip()
    assert view["mission"]["final_output"] == expected
    emit(
        tmp_path, "engineer.progress", 7, final_delivery=True,
        agent_layer="engineer", kind="agent_message", text="Late unrelated message",
    )
    assert load_mission_view(tmp_path)["mission"]["final_output"] == expected


@pytest.mark.parametrize("boundary", ["missing", "different", "resumed", "manager", "footer"])
def test_final_output_never_crosses_mission_boundaries(
    tmp_path: Path, boundary: str,
) -> None:
    if boundary != "missing":
        emit(tmp_path, "life.mission.started", 1, item_id="previous")
    emit(
        tmp_path, "engineer.progress", 2,
        kind="assistant_message", final_delivery=True, text="Previous mission output",
    )
    if boundary == "resumed":
        emit(tmp_path, "life.mission.started", 3, item_id="previous")
    elif boundary == "manager":
        emit(tmp_path, "life.manager.intent.started", 3, item_id="previous")
    elif boundary == "footer":
        emit(
            tmp_path, "engineer.progress", 3, kind="assistant_message",
            final_delivery=True, text="Decision:\nMILESTONE_STATUS=done",
        )
    item_id = "previous" if boundary in {"resumed", "manager", "footer"} else "current"
    view = emit(
        tmp_path, "life.mission.completed", 4,
        item_id=item_id, success=True, status="done",
    )
    assert view["mission"]["final_output"] == ""


@pytest.mark.parametrize("has_start", [True, False])
def test_legacy_snapshot_recovers_only_matching_final_delivery(
    tmp_path: Path, has_start: bool,
) -> None:
    events = [
        {"type": "engineer.progress", "ts": 2, "kind": "agent_message",
         "final_delivery": True, "text": "# Persisted handoff"},
        {"type": "life.mission.completed", "ts": 3, "item_id": "task",
         "status": "done", "success": True},
    ]
    if has_start:
        events.insert(0, {"type": "life.mission.started", "ts": 1, "item_id": "task"})
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
    )
    (tmp_path / "mission-view.json").write_text(json.dumps({
        "schema_version": 7, "bootstrapped": True, "last_event_ts": 10,
        "mission": {
            "id": "task", "status": "complete", "started_at": 1,
            "completed_at": 3, "summary": "Keep compact summary",
        },
        "stage": {"id": "delivery", "label": "Delivery"},
    }), encoding="utf-8")

    view = snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[])
    assert view["mission"]["final_output"] == ("# Persisted handoff" if has_start else "")
    assert view["mission"]["summary"] == "Keep compact summary"
    assert view["stage"] == {"id": "delivery", "label": "Delivery"}


@pytest.mark.parametrize("item_id", ["task", "next-task"])
def test_live_backlog_resume_cannot_inherit_final_output(tmp_path: Path, item_id: str) -> None:
    snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[])
    emit(tmp_path, "life.mission.started", 1, item_id="task")
    emit(
        tmp_path, "life.mission.completed", 2, item_id="task",
        summary="Previous summary", final_output="Previous output",
        success=True, status="done",
    )
    view = snapshot_mission_view(
        tmp_path, session={}, daemon={}, roles=[],
        backlog=[{"id": item_id, "status": "running", "started_ts": 3}],
    )
    assert view["mission"]["final_output"] == ""
    assert view["mission"]["summary"] == ""
    assert view["mission"]["started_at"] == 3
    assert load_mission_view(tmp_path)["mission"]["final_output"] == "Previous output"


def test_v4_snapshot_migrates_without_discarding_projected_state(tmp_path: Path) -> None:
    (tmp_path / "mission-view.json").write_text(
        json.dumps({
            "schema_version": 4,
            "bootstrapped": True,
            "mission": {"id": "kept", "title": "Keep this mission", "status": "working"},
            "stage": {"id": "delivery", "label": "Delivery"},
        }),
        encoding="utf-8",
    )

    view = snapshot_mission_view(
        tmp_path,
        session={},
        daemon={},
        roles=[],
        backlog=[],
    )

    assert view["schema_version"] == 7
    assert view["mission"]["id"] == "kept"
    assert view["routing"]["route"] == ""


def test_venue_and_idea_research_are_visible_as_engineer_work(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.mission.started",
        1,
        item_id="paper-1",
        title="Draft paper",
        objective="Prepare an ICLR submission",
    )
    view = emit(
        tmp_path,
        "venue.research.started",
        2,
        text="live web search: researching ICLR",
    )
    roles = {role["role"]: role for role in view["roles"]}
    assert view["active_role"] == "engineer"
    assert roles["engineer"]["status"] == "active"
    assert roles["engineer"]["kind"] == "venue_research_started"
    assert roles["engineer"]["label"] == "Studying what the target venue expects"
    assert view["role_work"][-1]["kind"] == "venue_research"

    view = emit(
        tmp_path,
        "venue.research.completed",
        3,
        ok=True,
        text="built research/VENUE_PROFILE.json",
    )
    roles = {role["role"]: role for role in view["roles"]}
    assert roles["engineer"]["kind"] == "venue_profile_ready"
    assert roles["engineer"]["label"] == "The target venue's expectations are written up."

    view = emit(
        tmp_path,
        "idea.search.started",
        4,
        text="live web search: seeding candidate ideas",
    )
    roles = {role["role"]: role for role in view["roles"]}
    assert view["active_role"] == "engineer"
    assert roles["engineer"]["kind"] == "idea_search_started"
    assert roles["engineer"]["label"] == "Searching for candidate research ideas"


def test_planner_terminal_event_clears_active_role(tmp_path: Path) -> None:
    view = emit(tmp_path, "life.planner.start", 1)
    assert view["active_role"] == "planner"

    view = emit(
        tmp_path,
        "life.planner.verdict",
        2,
        project_done=True,
        reason="reviewed project is complete",
    )

    roles = {role["role"]: role for role in view["roles"]}
    assert view["active_role"] == ""
    assert roles["planner"]["status"] == "done"
    assert roles["planner"]["kind"] == "project_finished"
    assert roles["planner"]["label"] == (
        "The project has reached its goal; no further tasks are planned."
    )
    assert view["timeline"][-1]["type"] == "life.planner.verdict"


def test_structured_events_build_reviewer_certified_achievement(tmp_path: Path) -> None:
    snapshot_mission_view(
        tmp_path,
        session={},
        daemon={},
        roles=[],
        backlog=[],
        continuous={},
        current_stage="research",
    )
    emit(
        tmp_path,
        "life.manager.intent.completed",
        1,
        intent_id="intent-1",
        item_id="task-1",
        objective="Optimize FlashAttention on B200",
        vertical="kernelbench",
        kind="optimize",
        stages=["research", "setup", "optimize", "measure", "report"],
        reason="bounded optimization campaign",
    )
    emit(
        tmp_path,
        "life.planner.task_added",
        2,
        item_id="task-1",
        title="Profile fused kernel",
        objective="Profile and improve the fused kernel",
        deps=[],
        branch_id="branch-1",
    )
    emit(
        tmp_path,
        "life.mission.started",
        3,
        item_id="task-1",
        title="Profile fused kernel",
        objective="Optimize FlashAttention on B200",
    )
    emit(tmp_path, "round.start", 4, round_index=7, round_max=24)
    emit(
        tmp_path,
        "engineer.progress",
        4.5,
        message_id="engineer-thought-1",
        kind="reasoning",
        agent_layer="engineer",
        text="Comparing the fused and unfused memory traffic.",
    )
    emit(
        tmp_path,
        "engineer.progress",
        4.6,
        message_id="engineer-tool-1",
        kind="tool_use",
        agent_layer="engineer",
        text="Inspecting the measured memory-traffic artifact.",
    )
    emit(
        tmp_path,
        "round.review.completed",
        9,
        round_index=7,
        status="done",
        reason="Official benchmark evidence verified.",
    )
    skill_path = tmp_path / "skills" / "fused-epilogue-playbook.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        "---\nname: fused-epilogue-playbook\ndescription: Reuse the fused epilogue.\n"
        "---\n\n# Fused epilogue\n\nKeep the measured memory-traffic evidence.\n",
        encoding="utf-8",
    )
    emit(
        tmp_path,
        "skill.evolution.completed",
        9.5,
        project_skill_dir=str(tmp_path / "skills"),
        global_skill_dir=str(tmp_path / "global-skills"),
        project_skill_count=1,
        global_skill_count=0,
    )
    emit(
        tmp_path,
        "skill.created",
        10,
        skill_id="skill-1",
        name="fused-epilogue-playbook",
        version=1,
        scope="engineer",
        path=str(skill_path),
    )
    completed = emit(
        tmp_path,
        "life.mission.completed",
        12,
        item_id="task-1",
        title="Profile fused kernel",
        objective="Optimize FlashAttention on B200",
        status="done",
        success=True,
    )
    assert completed["achievement"] is None
    view = emit(
        tmp_path,
        "research.achievement.certified",
        13,
        achievement_id="achievement-v7",
        title="Kernel gain certified",
        goal="Optimize FlashAttention on B200",
        summary="Reviewer accepted the official benchmark evidence.",
        evidence=["experiments/run-v7/result.json"],
        reviewer_certified=True,
    )

    view = snapshot_mission_view(
        tmp_path,
        session={},
        daemon={},
        roles=[],
        backlog=[],
        continuous={},
    )
    assert view["stage"]["id"] == "research"
    assert view["round"] == {"current": 7, "max": 24}
    assert view["achievement"]["title"] == "Kernel gain certified"
    assert view["achievement"]["evidence"] == ["experiments/run-v7/result.json"]
    assert view["achievement"]["skills_learned"] == 1
    assert view["achievement"]["artifacts"] == 1
    assert {row["role"] for row in view["role_work"]} >= {
        "manager",
        "planner",
        "engineer",
        "reviewer",
    }
    assert any(
        row["kind"] == "tool_use"
        and "measured memory-traffic" in row["detail"]
        for row in view["role_work"]
    )
    assert not any(row["kind"] == "reasoning" for row in view["role_work"])
    assert view["learned_skills"][0]["mission_id"] == "task-1"
    assert "# Fused epilogue" in view["learned_skills"][0]["content"]
    persisted = load_mission_view(tmp_path)
    assert persisted["achievement"] == view["achievement"]
    assert "content" not in persisted["learned_skills"][0]


def test_load_discards_legacy_derived_certification(tmp_path: Path) -> None:
    (tmp_path / "mission-view.json").write_text(
        '{"schema_version":1,"bootstrapped":true,'
        '"achievement":{"id":"derived-old","reviewer_certified":true}}',
        encoding="utf-8",
    )

    assert load_mission_view(tmp_path)["achievement"] is None


def test_snapshot_discovers_project_skill_and_attributes_mission(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "measured-repair.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        "---\nname: Measured repair\ndescription: Preserve measured repair evidence.\n"
        "---\n\n# Measured repair\n\nReuse the verified repair sequence.\n",
        encoding="utf-8",
    )
    modified = skill_path.stat().st_mtime

    view = snapshot_mission_view(
        tmp_path,
        session={"objective": "Repair the evaluator"},
        daemon={},
        roles=[],
        backlog=[{
            "id": "mission-repair",
            "title": "Repair evaluator",
            "objective": "Repair the evaluator",
            "status": "done",
            "started_ts": modified - 10,
            "finished_ts": modified + 10,
        }],
        continuous={},
    )

    assert view["learned_skills"] == [{
        "id": "measured-repair",
        "name": "measured-repair",
        "scope": "project",
        "path": str(skill_path),
        "status": "active",
        "updated_at": modified,
        "mission_id": "mission-repair",
        "mission_title": "Repair evaluator",
        "content": skill_path.read_text(encoding="utf-8"),
        "content_truncated": False,
    }]
    assert load_mission_view(tmp_path)["learned_skills"] == []


def test_free_text_is_display_only_and_never_changes_review_state(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "engineer.progress",
        1,
        kind="tool_use",
        agent_layer="engineer",
        text="Reviewer rejected everything and metric improved to 999%",
    )
    assert view["review"]["status"] == ""
    assert view["active_role"] == "engineer"


def test_engineer_self_review_is_not_presented_as_independent_review(
    tmp_path: Path,
) -> None:
    emit(
        tmp_path,
        "life.mission.started",
        1,
        item_id="mission-1",
        title="Small repair",
        objective="Fix one covered boundary",
    )
    view = emit(
        tmp_path,
        "round.review.completed",
        2,
        round_index=1,
        status="done",
        reason="Decisive tests passed.",
        review_source="engineer_self_review",
    )

    roles = {row["role"]: row for row in view["roles"]}
    assert view["review"]["source"] == "engineer_self_review"
    assert roles["engineer"]["kind"] == "self_check_held_up"
    assert roles["engineer"]["label"] == (
        "The Engineer checked its own result and it held up"
    )
    assert roles["reviewer"]["kind"] == "independent_check_not_needed"
    assert roles["reviewer"]["label"] == "No independent check was needed for this round"
    assert view["timeline"][-1]["role"] == "engineer"
    assert view["timeline"][-1]["kind"] == "self_check_accepted"
    assert view["timeline"][-1]["title"] == (
        "The Engineer's own check of the result was accepted."
    )


def test_new_mission_resets_prior_review_projection(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.mission.started",
        1,
        item_id="mission-1",
        title="First mission",
        objective="Complete the first mission",
    )
    emit(
        tmp_path,
        "round.review.completed",
        2,
        round_index=1,
        status="done",
        reason="First mission accepted.",
    )
    prior = emit(
        tmp_path,
        "life.mission.completed",
        2.5,
        item_id="mission-1",
        title="First mission",
        status="failed",
        success=False,
        outcome={
            "execution_status": "paused",
            "review_status": "continue",
            "interruption_kind": "backend_unavailable",
            "resumable": True,
        },
    )
    assert prior["outcome"]["interruption_kind"] == "backend_unavailable"

    view = emit(
        tmp_path,
        "life.mission.started",
        3,
        item_id="mission-2",
        title="Second mission",
        objective="Complete the second mission",
    )

    roles = {role["role"]: role for role in view["roles"]}
    assert view["mission"]["id"] == "mission-2"
    assert view["review"] == {"status": "", "reason": "", "rejected_attempts": 0}
    assert view["outcome"] == {}
    assert roles["reviewer"]["status"] == "waiting"
    assert roles["reviewer"]["kind"] == "awaiting_engineer"
    assert roles["reviewer"]["label"] == (
        "Waiting for the Engineer to finish a round before checking it"
    )
    assert roles["engineer"]["status"] == "active"
    assert view["active_role"] == "engineer"


@pytest.mark.parametrize("status", ["continue", "blocked", "done"])
def test_skipped_review_is_not_a_verdict_or_rejected_attempt(tmp_path: Path, status: str) -> None:
    emit(tmp_path, "round.review.completed", 1, status="continue", reason="Add a control.")
    view = emit(
        tmp_path,
        "round.review.completed",
        2,
        round_index=2,
        status=status,
        reason="Session turn allowance exhausted; review did not run.",
        next_action="Resume from the saved checkpoint.",
        review_skipped=True,
    )
    assert view["review"]["status"] == "skipped"
    assert view["review"]["rejected_attempts"] == 1
    entry = view["timeline"][-1]
    assert entry["kind"] == "round_not_judged"
    assert entry["title"] == "This round was not judged."
    assert entry["tone"] == "info"
    assert entry["cause"] == "unknown"
    assert entry["detail"] == "The work did not reach a result that could be checked."
    # The runtime's own record stays beside the sentence, never inside it.
    assert entry["technical"] == "Session turn allowance exhausted; review did not run."
    work = view["role_work"][-1]
    assert work["status"] == "skipped"
    assert work["kind"] == "review"
    assert work["title"] == entry["title"]
    assert work["detail"] == entry["detail"]
    assert work["technical"] == entry["technical"]
    assert "saved checkpoint" not in work["detail"]
    reviewer = next(role for role in view["roles"] if role["role"] == "reviewer")
    assert reviewer["status"] == "waiting"
    assert reviewer["kind"] == "round_not_judged"


def test_new_review_clears_prior_verdict_without_erasing_history(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "round.review.completed",
        1,
        status="continue",
        reason="Add a control.",
        manuscript_snapshot={"path": "old-paper.md"},
    )
    view = emit(tmp_path, "round.review.started", 2, round_index=2)
    assert view["review"] == {"status": "", "reason": "", "rejected_attempts": 1}
    assert view["active_role"] == "reviewer"
    assert any(item["detail"] == "Add a control." for item in view["timeline"])
    assert any(item["detail"] == "Add a control." for item in view["role_work"])


def test_snapshot_corrects_old_review_projection_without_rewriting_it(tmp_path: Path) -> None:
    events = [
        {"type": "life.mission.started", "ts": 1, "item_id": "current"},
        {"type": "round.review.completed", "ts": 2, "event_id": "skipped",
         "status": "continue", "reason": "Turn allowance reached.", "review_skipped": True},
        {"type": "round.review.started", "ts": 3, "round_index": 2},
    ]
    for event in events:
        view = update_mission_view_event(tmp_path, event)
    view["bootstrapped"] = True
    view["review"] = {"status": "continue", "reason": "Turn allowance reached.", "rejected_attempts": 1}
    for row in view["timeline"]:
        if row["kind"] == "round_not_judged":
            row.update(
                kind="another_attempt_requested",
                title="The results were not accepted; another attempt was requested.",
                tone="error",
            )
    for row in view["role_work"]:
        if row["kind"] == "review" and row["status"] == "skipped":
            row.update(
                title="The results were not accepted; another attempt was requested.",
                status="continue",
                kind="verdict",
            )
    view_file = tmp_path / "mission-view.json"
    event_file = tmp_path / "events.jsonl"
    view_file.write_text(json.dumps(view))
    event_file.write_text("".join(json.dumps(event) + "\n" for event in events))
    before = view_file.read_bytes()

    def snapshot():
        return snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[], continuous={})

    result = snapshot()
    assert result["review"] == {"status": "", "reason": "", "rejected_attempts": 0}
    assert any(
        row["kind"] == "round_not_judged"
        and row["title"] == "This round was not judged."
        and row["tone"] == "info"
        for row in result["timeline"]
    )
    assert any(
        row["kind"] == "review"
        and row["status"] == "skipped"
        and row["title"] == "This round was not judged."
        for row in result["role_work"]
    )
    assert view_file.read_bytes() == before

    # A later real rejection invalidates the read cache while the older writer
    # still incorrectly includes its earlier skipped attempt in the counter.
    event_file.write_text(event_file.read_text() + json.dumps({
        "type": "round.review.completed", "ts": 4, "status": "continue", "reason": "Add a control.",
    }) + "\n")
    view["review"] = {"status": "continue", "reason": "Add a control.", "rejected_attempts": 2}
    view_file.write_text(json.dumps(view))
    before = view_file.read_bytes()
    result = snapshot()
    assert result["review"]["rejected_attempts"] == 1
    assert result["review"]["reason"] == "Add a control."
    assert view_file.read_bytes() == before

    # A tail that belongs to another mission must not overwrite this snapshot.
    view["mission"].update(id="new-mission", started_at=5)
    view["review"] = {"status": "", "reason": "", "rejected_attempts": 0}
    view_file.write_text(json.dumps(view))
    assert snapshot()["review"] == view["review"]

    # Live ownership can advance before the old daemon writes its projection.
    view["mission"].update(id="current", started_at=1)
    view["review"] = {"status": "continue", "reason": "Old verdict.", "rejected_attempts": 2}
    view_file.write_text(json.dumps(view))
    event_file.write_text(event_file.read_text() + json.dumps({
        "type": "life.mission.started", "ts": 5, "item_id": "new-mission",
    }) + "\n" + json.dumps({
        "type": "round.review.completed", "ts": 6, "item_id": "current",
        "status": "continue", "reason": "Late event from the prior task.",
    }) + "\n")
    before = view_file.read_bytes()
    result = snapshot_mission_view(
        tmp_path, session={}, daemon={"alive": True},
        roles=[{"role": "engineer", "active": True}],
        backlog=[{"id": "new-mission", "status": "running", "started_ts": 5}],
        continuous={},
    )
    assert result["mission"]["id"] == "new-mission"
    assert result["review"] == {"status": "", "reason": "", "rejected_attempts": 0}
    assert result["active_role"] == "engineer"
    assert view_file.read_bytes() == before


def test_review_replay_does_not_bridge_a_truncated_current_log(tmp_path: Path, monkeypatch) -> None:
    from argus_skill.core.mission_view import _snapshot

    view = emit(tmp_path, "life.mission.started", 1, item_id="current")
    view["bootstrapped"] = True
    view["review"] = {"status": "continue", "reason": "Retained verdict", "rejected_attempts": 3}
    (tmp_path / "mission-view.json").write_text(json.dumps(view))
    (tmp_path / "events.jsonl.1").write_text(json.dumps({
        "type": "life.mission.started", "ts": 1, "item_id": "current",
    }) + "\n")
    (tmp_path / "events.jsonl").write_text(" " * 100 + "\n" + json.dumps({
        "type": "round.review.completed", "ts": 4, "item_id": "current",
        "status": "continue", "reason": "Latest verdict",
    }) + "\n")
    monkeypatch.setattr(_snapshot, "MISSION_BOOTSTRAP_MAX_BYTES", 64)
    result = snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[], continuous={})
    assert result["review"] == view["review"]


def test_review_snapshot_uses_event_identity_despite_earlier_claim_timestamp(tmp_path: Path) -> None:
    events = [
        {"type": "life.mission.started", "ts": 10.004, "item_id": "current"},
        {"type": "round.review.completed", "ts": 11, "item_id": "current",
         "status": "continue", "reason": "Check the real control.", "review_skipped": False},
    ]
    for event in events:
        view = update_mission_view_event(tmp_path, event)
    view["bootstrapped"] = True
    view["review"]["rejected_attempts"] = 2
    view_file = tmp_path / "mission-view.json"
    view_file.write_text(json.dumps(view))
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    before = view_file.read_bytes()
    kwargs = dict(session={}, daemon={"alive": True}, roles=[], continuous={})
    result = snapshot_mission_view(tmp_path, backlog=[{
        "id": "current", "status": "running", "started_ts": 10.0,
    }], **kwargs)
    assert result["review"]["reason"] == "Check the real control."
    assert result["review"]["rejected_attempts"] == 1
    assert view_file.read_bytes() == before

    # The next claim of the same task must not inherit the earlier verdict.
    result = snapshot_mission_view(tmp_path, backlog=[{
        "id": "current", "status": "running", "started_ts": 12.0,
    }], **kwargs)
    assert result["review"] == {"status": "", "reason": "", "rejected_attempts": 0}


def test_snapshot_keeps_current_mission_owner_ahead_of_stale_pending_work(
    tmp_path: Path,
) -> None:
    snapshot_mission_view(
        tmp_path,
        session={},
        daemon={},
        roles=[],
        backlog=[],
        continuous={},
    )
    emit(
        tmp_path,
        "life.mission.started",
        10,
        item_id="deploy-verify",
        title="Verify deployed release",
        objective="Verify the current deployed release.",
    )
    emit(
        tmp_path,
        "life.mission.completed",
        20,
        item_id="deploy-verify",
        title="Verify deployed release",
        objective="Verify the current deployed release.",
        status="blocked",
        success=False,
        outcome_class="blocked",
    )

    view = snapshot_mission_view(
        tmp_path,
        session={"objective": ""},
        daemon={"alive": True},
        roles=[],
        backlog=[
            {
                "id": "old-research",
                "title": "Preparing exact-search foundations",
                "objective": "Continue the old research task.",
                "status": "pending",
                "deps": [],
            },
            {
                "id": "deploy-verify",
                "title": "Verify deployed release",
                "objective": "Verify the current deployed release.",
                "status": "paused_operator",
                "deps": [],
            },
        ],
        continuous={
            "enabled": False,
            "objective": "",
            "done_reason": "operator drain-stop",
        },
    )

    assert view["mission"]["id"] == "deploy-verify"
    assert view["mission"]["title"] == "Verify deployed release"
    assert view["mission"]["objective"] == "Verify the current deployed release."
    assert view["mission"]["status"] == "blocked"


def test_review_deferral_projects_as_engineer_activity(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "round.review.deferred",
        1,
        round_index=2,
        next_step="wire the parser into the runner",
        deferral_count=1,
        deferral_limit=1,
    )

    assert view["active_role"] == "engineer"
    roles = {role["role"]: role for role in view["roles"]}
    assert roles["engineer"]["kind"] == "continuing_before_check"
    assert roles["engineer"]["label"] == (
        "Continuing to the next round before the results are checked"
    )
    assert roles["reviewer"]["status"] == "waiting"
    assert view["timeline"][-1]["detail"] == "wire the parser into the runner"


@pytest.mark.parametrize(
    ("status", "success", "mission_status", "role_status", "kind", "label", "tone"),
    [
        (
            "done", True, "complete", "done",
            "mission_completed", "The task was completed.", "success",
        ),
        (
            "completed", False, "complete", "done",
            "mission_completed", "The task was completed.", "success",
        ),
        (
            "research_incomplete", False, "incomplete", "done",
            "mission_incomplete", "The task stopped with work still remaining.", "info",
        ),
        (
            "no_progress", False, "stalled", "done",
            "mission_stalled",
            "The task stopped because recent rounds made no useful progress.",
            "info",
        ),
        (
            "blocked", False, "blocked", "error",
            "mission_blocked",
            "The task cannot continue until something outside it is resolved.",
            "error",
        ),
        (
            "failed", False, "failed", "error",
            "mission_failed", "The task could not be completed.", "error",
        ),
        (
            "legacy_unknown_status", False, "ended", "done",
            "mission_ended", "The task ended without a recorded outcome.", "info",
        ),
    ],
)
def test_completed_mission_projects_terminal_outcomes_without_false_failures(
    tmp_path: Path,
    status: str,
    success: bool,
    mission_status: str,
    role_status: str,
    kind: str,
    label: str,
    tone: str,
) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        title="Run mission",
        status=status,
        success=success,
    )

    role = next(role for role in view["roles"] if role["role"] == "engineer")
    timeline = view["timeline"][-1]
    assert view["mission"]["status"] == mission_status
    assert role["status"] == role_status
    assert role["kind"] == kind
    assert role["label"] == label
    assert timeline["kind"] == kind
    assert timeline["title"] == label
    assert timeline["tone"] == tone
    # A status the projection does not recognise is kept as a technical fact,
    # not written into the sentence.
    assert timeline.get("technical", "") == (status if kind == "mission_ended" else "")
    assert load_mission_view(tmp_path)["mission"]["status"] == mission_status


def test_completed_mission_prefers_normalized_outcome_class(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        status="legacy_unknown_status",
        success=False,
        outcome_class="incomplete",
    )

    assert view["mission"]["status"] == "incomplete"
    assert view["timeline"][-1]["kind"] == "mission_incomplete"
    assert view["timeline"][-1]["title"] == "The task stopped with work still remaining."


def test_completed_mission_projects_engineer_summary(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-summary",
        title="Create result",
        status="done",
        success=True,
        summary="Created RESULT.txt and verified its exact contents.",
    )

    assert view["mission"]["summary"] == (
        "Created RESULT.txt and verified its exact contents."
    )
    assert view["timeline"][-1]["detail"] == (
        "Created RESULT.txt and verified its exact contents."
    )
    assert view["role_work"][-1]["detail"] == (
        "Created RESULT.txt and verified its exact contents."
    )


def test_final_submission_projects_as_certified_not_merely_completed(
    tmp_path: Path,
) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-final",
        title="Prepare final ICLR submission",
        status="done",
        success=True,
        final_submission_certified=True,
    )

    role = next(role for role in view["roles"] if role["role"] == "engineer")
    assert role["kind"] == "mission_certified"
    assert role["label"] == "The final submission was checked and approved."
    assert view["timeline"][-1]["title"] == "The final submission was checked and approved."


def test_nested_submission_flag_does_not_claim_certification(
    tmp_path: Path,
) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-draft",
        title="Prepare draft",
        status="done",
        success=True,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "certified",
            "final_submission_certified": True,
        },
    )

    role = next(role for role in view["roles"] if role["role"] == "engineer")
    assert role["kind"] == "mission_completed"
    assert role["label"] == "The task was completed."


def test_completed_mission_preserves_stage_outcome(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        status="done",
        success=True,
        outcome_class="completed",
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_certified",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert view["mission"]["status"] == "complete"
    assert view["outcome"]["stage_certification"] == "not_certified"
    assert load_mission_view(tmp_path)["outcome"] == view["outcome"]
def test_reviewer_handoff_leaves_only_reviewer_active(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "engineer.progress",
        1,
        kind="agent_message",
        agent_layer="engineer",
        text="Engineer result",
    )

    view = emit(tmp_path, "round.review.started", 2, round_index=1)

    roles = {role["role"]: role for role in view["roles"]}
    assert view["active_role"] == "reviewer"
    assert roles["reviewer"]["status"] == "active"
    assert roles["engineer"]["status"] == "done"


def test_campaign_clock_does_not_reset_between_dag_nodes(tmp_path: Path) -> None:
    first = emit(
        tmp_path,
        "life.mission.started",
        10,
        item_id="scope",
        title="Scope",
        objective="Scope node",
    )
    assert first["mission"]["campaign_started_at"] == 10
    assert first["mission"]["started_at"] == 10

    emit(
        tmp_path,
        "life.mission.completed",
        20,
        item_id="scope",
        title="Scope",
        success=True,
        status="done",
    )
    second = emit(
        tmp_path,
        "life.mission.started",
        100,
        item_id="solve",
        title="Solve",
        objective="Solve node",
    )

    assert second["mission"]["campaign_started_at"] == 10
    assert second["mission"]["started_at"] == 100


def test_snapshot_bootstraps_from_existing_event_log(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text(
        "\n".join([
            '{"type":"life.mission.started","ts":1,"item_id":"task-1","title":"Existing mission","objective":"Recover me"}',
            '{"type":"round.start","ts":2,"round_index":3,"round_max":9}',
        ]) + "\n",
        encoding="utf-8",
    )
    view = snapshot_mission_view(
        tmp_path,
        session={"id": "s-1", "objective": ""},
        daemon={"alive": True},
        roles=[],
        backlog=[],
        continuous={"enabled": False, "objective": ""},
        current_stage="optimize",
    )
    assert view["bootstrapped"] is True
    assert view["mission"]["title"] == "Existing mission"
    assert view["round"] == {"current": 3, "max": 9}
    assert view["stage"]["id"] == "optimize"


def test_snapshot_keeps_completed_mission_status_while_daemon_idles(
    tmp_path: Path,
) -> None:
    (tmp_path / "events.jsonl").write_text(
        "\n".join([
            '{"type":"life.mission.started","ts":10,"item_id":"bounded-1",'
            '"title":"Bounded task","objective":"finish once"}',
            '{"type":"life.mission.completed","ts":20,"item_id":"bounded-1",'
            '"status":"done","success":true}',
        ]) + "\n",
        encoding="utf-8",
    )

    view = snapshot_mission_view(
        tmp_path,
        session={"id": "s-done", "objective": ""},
        daemon={"alive": True},
        roles=[],
        backlog=[],
        continuous={"enabled": False, "objective": ""},
    )

    assert view["mission"]["status"] == "complete"
    assert view["mission"]["completed_at"] == 20


def test_snapshot_hides_stale_pipeline_stage_without_a_mission(tmp_path: Path) -> None:
    view = snapshot_mission_view(
        tmp_path,
        session={"id": "s-idle", "objective": ""},
        daemon={"alive": False},
        roles=[],
        backlog=[],
        continuous={"enabled": False, "objective": ""},
        current_stage="findings_report",
    )

    assert view["mission"]["status"] == "idle"
    assert view["stage"] == {"id": "", "label": ""}

def test_live_role_overlay_does_not_corrupt_event_sourced_role_state(
    tmp_path: Path,
) -> None:
    (tmp_path / "events.jsonl").write_text(
        "\n".join([
            '{"type":"life.manager.intent.completed","ts":1,'
            '"item_id":"task-1","objective":"Write the paper",'
            '"reason":"goal framed"}',
            '{"type":"life.mission.started","ts":2,"item_id":"task-1",'
            '"title":"Write the paper","objective":"Write the paper"}',
        ]) + "\n",
        encoding="utf-8",
    )
    backlog = [{
        "id": "task-1",
        "title": "Write the paper",
        "objective": "Write the paper",
        "status": "running",
    }]

    transient = snapshot_mission_view(
        tmp_path,
        session={"id": "s-live", "objective": ""},
        daemon={"alive": True},
        roles=[{
            "role": "manager",
            "active": True,
            "label": "auditing framework health",
            "backend": "copilot",
            "model": "gpt",
            "effort": "high",
            "age_s": 0,
        }],
        backlog=backlog,
        continuous={"enabled": False, "objective": ""},
    )
    assert next(
        role for role in transient["roles"] if role["role"] == "manager"
    )["status"] == "active"

    resumed = snapshot_mission_view(
        tmp_path,
        session={"id": "s-live", "objective": ""},
        daemon={"alive": True},
        roles=[{
            "role": "engineer",
            "active": True,
            "label": "editing manuscript",
            "backend": "copilot",
            "model": "gpt",
            "effort": "high",
            "age_s": 0,
        }],
        backlog=backlog,
        continuous={"enabled": False, "objective": ""},
    )
    roles = {role["role"]: role for role in resumed["roles"]}

    assert roles["manager"]["status"] == "done"
    assert roles["manager"]["kind"] == "goal_framed"
    assert roles["manager"]["label"] == (
        "The request has been understood and the goal for this project is set."
    )
    assert roles["engineer"]["status"] == "active"


def test_evolution_events_project_skill_and_wiki_storage(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "skill.evolution.completed",
        1,
        ops_proposed=1,
        created=1,
        updated=0,
        archived=0,
        rejected=0,
        project_skill_dir="/state/project/skills",
        global_skill_dir="/state/global/skills",
        project_skill_count=3,
        global_skill_count=20,
    )
    view = emit(
        tmp_path,
        "wiki.evolution.completed",
        2,
        wiki_count=1,
        ops_proposed=1,
        paths=["/workspace/.autors/demo/wiki"],
    )
    emit(
        tmp_path,
        "wiki.created",
        3,
        page_id="retry-pattern",
        card_type="pattern",
        title="Bounded retry pattern",
        status="scratch",
        path="/workspace/.autors/demo/wiki/pages/patterns/retry-pattern.md",
    )
    view = emit(
        tmp_path,
        "wiki.promotion.promoted",
        4,
        page_id="retry-pattern",
        card_type="patterns",
        from_status="scratch",
        to_status="candidate",
    )
    emit(
        tmp_path,
        "skill.history.compressed",
        5,
        count=3,
        keep_hot=20,
        bytes_saved=1000,
    )
    view = emit(
        tmp_path,
        "wiki.retired.compressed",
        6,
        count=2,
        keep_hot=20,
        bytes_saved=500,
    )

    assert view["storage"] == {
        "project_skill_dir": "/state/project/skills",
        "global_skill_dir": "/state/global/skills",
        "project_skill_count": 3,
        "global_skill_count": 20,
        "skill_history_compressed": 3,
        "wiki_retired_compressed": 2,
        "skill_history_bytes_saved": 1000,
        "wiki_retired_bytes_saved": 500,
        "wiki_paths": ["/workspace/.autors/demo/wiki"],
    }
    assert view["learned_wiki_pages"][0]["title"] == "Bounded retry pattern"
    assert view["learned_wiki_pages"][0]["status"] == "candidate"
    assert view["timeline"][-1]["kind"] == "knowledge_promoted"
    assert view["timeline"][-1]["title"] == "A knowledge page was promoted."


def test_skill_source_promotion_updates_capability_projection(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "skill.created",
        1,
        skill_id="s1",
        name="bounded retry",
        version=1,
        path="/state/project/skills/bounded-retry.md",
    )
    view = emit(
        tmp_path,
        "skill.tidied",
        2,
        name="bounded retry",
        placement="vertical",
        vertical="kernelbench",
        path="/source/verticals/kernelbench/skills/bounded-retry.md",
        text="promoted",
    )

    skill = view["learned_skills"][0]
    assert skill["source_placement"] == "vertical"
    assert skill["source_vertical"] == "kernelbench"
    assert skill["source_path"].endswith("bounded-retry.md")
    assert view["timeline"][-1]["kind"] == "capability_shared"
    assert view["timeline"][-1]["title"] == (
        "A capability was promoted for use across projects."
    )


def test_chinese_request_gets_chinese_sentences_with_stable_codes(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.manager.intent.completed",
        1,
        item_id="task-1",
        objective="给我写一篇iclr的论文",
        vertical="research",
        current_stage="idea",
        reason="The request asks for a full paper.",
    )
    view = emit(
        tmp_path,
        "life.mission.started",
        2,
        item_id="task-1",
        title="Build the source-backed twelve-route idea portfolio",
        objective="Build the source-backed twelve-route idea portfolio",
    )

    roles = {role["role"]: role for role in view["roles"]}
    assert view["language"] == "zh"
    assert view["stage"] == {"id": "idea", "label": "选题"}
    assert roles["manager"]["kind"] == "goal_framed"
    assert roles["manager"]["label"] == "已经理解这项请求，项目目标已经确定。"
    # The Planner wrote this task in English; the operator asked in Chinese,
    # and the operator's language wins.
    assert roles["engineer"]["kind"] == "mission_started"
    assert roles["engineer"]["label"] == "开始处理这项任务"
    assert roles["reviewer"]["label"] == "等待工程师完成一轮工作后再核对"

    view = emit(tmp_path, "round.start", 3, round_index=9, round_max=0)
    assert view["timeline"][-1]["kind"] == "round_started"
    assert view["timeline"][-1]["title"] == "第 9 轮工作开始。"


def test_round_the_model_service_dropped_is_explained_without_runtime_words(
    tmp_path: Path,
) -> None:
    emit(
        tmp_path,
        "life.manager.intent.completed",
        1,
        item_id="task-1",
        objective="给我写一篇iclr的论文",
    )
    emit(tmp_path, "life.mission.started", 2, item_id="task-1", title="Portfolio")
    raw = (
        "Engineer backend failed before a trustworthy completed turn; "
        "reviewer skipped. backend_failure_streak=9/2; "
        "error=Copilot CLI exited with code 1."
    )
    view = emit(
        tmp_path,
        "round.review.completed",
        3,
        round_index=9,
        status="continue",
        reason=raw,
        next_action="Retry in a fresh Codex session; do not resume the failed thread.",
        review_source="reviewer",
        backend_unavailable=False,
        stop_kind=None,
        text=f"review: skipped (backend failure) — {raw}",
        review_skipped=True,
        item_id="task-1",
    )

    entry = view["timeline"][-1]
    assert entry["kind"] == "round_not_judged"
    assert entry["cause"] == "engineer_service_dropped"
    assert entry["title"] == "这一轮没有人审阅。"
    assert entry["detail"] == (
        "模型服务在工程师得出可核对的结果之前中断了会话，因此没有可以审阅的内容。"
        "Argus 会换一个新会话重试。"
    )
    assert entry["technical"] == (
        "backend_failure_streak=9/2; error=Copilot CLI exited with code 1."
    )
    for text in (entry["title"], entry["detail"]):
        assert "backend" not in text.lower()
        assert "streak" not in text.lower()
        assert "reviewer skipped" not in text.lower()
    work = view["role_work"][-1]
    assert work["cause"] == "engineer_service_dropped"
    assert work["technical"] == entry["technical"]
    assert work["detail"] == entry["detail"]
    # The raw sentence is still available as the projection's record.
    assert view["review"]["reason"] == raw


def test_round_skipped_for_a_pause_names_the_reason(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "round.review.completed",
        1,
        status="blocked",
        reason="Backend call paused (stop_kind=budget_exhausted); reviewer skipped. error=exit=0",
        stop_kind="budget_exhausted",
        backend_unavailable=True,
        text="review: skipped (budget_exhausted)",
        review_skipped=True,
    )
    entry = view["timeline"][-1]
    assert entry["cause"] == "paused"
    assert entry["detail"] == (
        "The work was paused before the Engineer finished this round because "
        "the project reached its budget limit; it resumes from the saved progress."
    )
    assert entry["technical"] == "stop_kind=budget_exhausted; error=exit=0"


def test_round_skipped_because_argus_stopped_is_classified_by_stop_kind(
    tmp_path: Path,
) -> None:
    view = emit(
        tmp_path,
        "round.review.completed",
        1,
        status="blocked",
        reason="Argus was stopped by its operator in the middle of this round. Technical record: error=daemon stop requested",
        stop_kind="daemon_shutdown",
        text="review: skipped (daemon stop requested)",
        review_skipped=True,
    )
    entry = view["timeline"][-1]
    assert entry["cause"] == "argus_stopped"
    assert entry["detail"] == (
        "Argus was stopped while this round was running; the work so far is saved."
    )
    assert entry["technical"] == "error=daemon stop requested"


def test_paused_mission_is_described_as_paused_not_as_a_status_code(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.manager.intent.completed",
        1,
        item_id="task-1",
        objective="给我写一篇iclr的论文",
    )
    view = emit(
        tmp_path,
        "life.mission.completed",
        2,
        item_id="task-1",
        title="Portfolio",
        success=False,
        status="paused_daemon_shutdown",
        summary="Existing team storage has source snapshots but no route reports yet.",
        outcome_class="ended",
        outcome={
            "execution_status": "paused",
            "review_status": "continue",
            "interruption_kind": "backend_unavailable",
            "resumable": True,
        },
        stop_kind="backend_unavailable",
        resumable=True,
    )
    role = next(role for role in view["roles"] if role["role"] == "engineer")
    entry = view["timeline"][-1]
    assert view["mission"]["status"] == "ended"
    assert role["kind"] == "mission_paused"
    assert role["label"] == "任务在完成前暂停，因为 Argus 被停止；进度已保存，可以继续。"
    assert entry["kind"] == "mission_paused"
    assert entry["title"] == role["label"]
    assert entry["technical"] == "paused_daemon_shutdown"
    assert "paused_daemon_shutdown" not in entry["title"]
    assert entry["detail"] == (
        "Existing team storage has source snapshots but no route reports yet."
    )
    assert view["role_work"][-1]["technical"] == "paused_daemon_shutdown"


def test_english_paused_mission_names_the_reason(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        title="Run the benchmark",
        success=False,
        status="paused_budget",
    )
    assert view["timeline"][-1]["title"] == (
        "The task was paused before it finished because the project reached "
        "its budget limit; its progress is saved and it can be resumed."
    )


def test_version_six_view_is_rebuilt_with_codes_and_sentences(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "ts": 1,
            "item_id": "task-1",
            "objective": "给我写一篇iclr的论文",
        })
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "mission-view.json").write_text(
        json.dumps({
            "schema_version": 6,
            "bootstrapped": True,
            "roles": [{"role": "manager", "status": "done", "label": "Goal framed"}],
            "timeline": [{"id": "old", "title": "Goal framed", "type": "life.manager.intent.completed"}],
        }),
        encoding="utf-8",
    )

    view = snapshot_mission_view(tmp_path, session={}, daemon={}, roles=[], backlog=[])

    assert view["schema_version"] == 7
    assert view["language"] == "zh"
    manager = next(role for role in view["roles"] if role["role"] == "manager")
    assert manager["kind"] == "goal_framed"
    assert manager["label"] == "已经理解这项请求，项目目标已经确定。"
    assert all(row.get("kind") for row in view["timeline"])
    assert load_mission_view(tmp_path)["schema_version"] == 7


def test_failed_task_closing_row_explains_the_runtime_record(tmp_path: Path) -> None:
    emit(
        tmp_path,
        "life.manager.intent.completed",
        1,
        item_id="task-1",
        objective="给我写一篇iclr的论文",
    )
    raw = (
        "Reviewer backend unavailable for 2 consecutive attempt(s); failing loud "
        "rather than settling the round without a real review. The Reviewer's "
        "session ended before it reached a conclusion, so this round was not "
        "judged. Runner receipt: exit=1, fatal_error=Copilot CLI exited with code 1."
    )
    view = emit(
        tmp_path,
        "life.mission.completed",
        2,
        item_id="task-1",
        title="Portfolio",
        success=False,
        status="failed",
        failure_reason=raw,
        stop_reason=raw,
    )
    entry = view["timeline"][-1]
    assert entry["kind"] == "mission_failed"
    assert entry["title"] == "任务没能完成。"
    assert entry["cause"] == "reviewer_unreachable"
    assert entry["detail"] == (
        "审阅者连续 2 次没能给出判断，Argus 选择停下这项任务，"
        "而不是在没有真正审阅的情况下结束一轮。"
    )
    assert entry["technical"] == "exit=1, fatal_error=Copilot CLI exited with code 1."
    assert view["role_work"][-1]["cause"] == "reviewer_unreachable"
    assert view["role_work"][-1]["detail"] == entry["detail"]


def test_failed_task_keeps_a_roles_own_reason_verbatim(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        title="Prove the lemma",
        success=False,
        status="failed",
        failure_reason="The proof relies on a lemma that does not hold for n = 0.",
    )
    entry = view["timeline"][-1]
    assert entry["detail"] == "The proof relies on a lemma that does not hold for n = 0."
    assert "cause" not in entry
    assert "technical" not in entry


def test_failed_task_with_new_runtime_wording_is_explained_in_english(tmp_path: Path) -> None:
    view = emit(
        tmp_path,
        "life.mission.completed",
        1,
        item_id="task-1",
        title="Run the sweep",
        success=False,
        status="error",
        failure_reason=(
            "The model service dropped the Engineer's session before it produced a "
            "result that could be checked, so this round was not judged; Argus "
            "retries in a fresh session. Technical record: consecutive failures=2, "
            "limit=2, error=Copilot CLI exited with code 1."
        ),
    )
    entry = view["timeline"][-1]
    assert entry["cause"] == "engineer_service_dropped"
    assert entry["detail"] == (
        "The model service kept dropping the Engineer's session before it produced "
        "a result that could be checked, so Argus stopped this task; it can be "
        "retried later."
    )
    assert entry["technical"] == (
        "consecutive failures=2, limit=2, error=Copilot CLI exited with code 1."
    )
