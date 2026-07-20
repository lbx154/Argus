from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core.mission_view import (
    load_mission_view,
    snapshot_mission_view,
    update_mission_view_event,
)


def emit(root: Path, event_type: str, ts: float, **payload) -> dict:
    return update_mission_view_event(root, {"type": event_type, "ts": ts, **payload})


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
    assert roles["planner"]["label"] == "Project reviewed"
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
        "research.hypothesis.proposed",
        5,
        hypothesis_id="hyp-1",
        title="Fuse the epilogue",
        statement="A fused epilogue should reduce memory traffic.",
        branch_id="branch-1",
    )
    emit(
        tmp_path,
        "research.experiment.started",
        6,
        experiment_id="exp-v7",
        title="Kernel v7",
        hypothesis_id="hyp-1",
        branch_id="branch-1",
    )
    emit(
        tmp_path,
        "research.experiment.completed",
        7,
        experiment_id="exp-v7",
        status="completed",
        summary="Official scorer passed.",
        evidence=["experiments/run-v7/result.json"],
    )
    emit(
        tmp_path,
        "research.metric.reported",
        8,
        metric_id="metric-v7",
        name="sol_percent",
        baseline=49.4,
        value=61.8,
        unit="%",
        direction="maximize",
        evidence="experiments/run-v7/result.json",
        experiment_id="exp-v7",
        round_index=7,
        primary=True,
    )
    emit(
        tmp_path,
        "round.review.completed",
        9,
        round_index=7,
        status="done",
        reason="Official benchmark evidence verified.",
        planner_report={"plan_signal": "continue", "reason": "Evidence is complete."},
        checklist_feedback={"run.evidence": {"status": "pass"}},
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
    emit(
        tmp_path,
        "research.artifact.registered",
        11,
        artifact_id="artifact-1",
        path="experiments/run-v7/result.json",
        kind="data",
        title="Kernel v7 result",
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
        metric_id="metric-v7",
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
    assert view["primary_metric"]["value"] == 61.8
    assert view["primary_metric"]["verification_status"] == "accepted"
    assert view["achievement"]["baseline"] == 49.4
    assert view["achievement"]["best"] == 61.8
    assert view["achievement"]["gain"] == pytest.approx(12.4)
    assert view["achievement"]["experiments_run"] == 1
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
    assert view["decision_context"]["planner_report"]["plan_signal"] == "continue"
    assert view["decision_context"]["checklist_feedback"]["run.evidence"]["status"] == "pass"
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
        "name": "Measured repair",
        "version": 1,
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
    assert view["primary_metric"] is None
    assert view["active_role"] == "engineer"


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
        planner_report={"plan_signal": "continue"},
    )

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
    assert view["decision_context"] == {}
    assert roles["reviewer"]["status"] == "waiting"
    assert roles["reviewer"]["label"] == "Awaiting engineer handoff"
    assert roles["engineer"]["status"] == "active"
    assert view["active_role"] == "engineer"


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
    assert roles["engineer"]["label"] == "Continuing before review"
    assert roles["reviewer"]["status"] == "waiting"
    assert view["timeline"][-1]["detail"] == "wire the parser into the runner"


@pytest.mark.parametrize(
    ("status", "success", "mission_status", "role_status", "label", "tone"),
    [
        ("done", True, "complete", "done", "Mission completed", "success"),
        ("completed", False, "complete", "done", "Mission completed", "success"),
        ("research_incomplete", False, "incomplete", "done", "Mission incomplete", "info"),
        ("no_progress", False, "stalled", "done", "Mission stalled", "info"),
        ("blocked", False, "blocked", "error", "Mission blocked", "error"),
        ("failed", False, "failed", "error", "Mission failed", "error"),
        (
            "legacy_unknown_status",
            False,
            "ended",
            "done",
            "Mission ended · legacy_unknown_status",
            "info",
        ),
    ],
)
def test_completed_mission_projects_terminal_outcomes_without_false_failures(
    tmp_path: Path,
    status: str,
    success: bool,
    mission_status: str,
    role_status: str,
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
    assert role["label"] == label
    assert timeline["title"] == label
    assert timeline["tone"] == tone
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
    assert view["timeline"][-1]["title"] == "Mission incomplete"


def test_completed_mission_preserves_stage_and_scientific_outcome(tmp_path: Path) -> None:
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
            "scientific_decision": "no_go",
            "failure_source": "scientific_evidence_failure",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert view["mission"]["status"] == "complete"
    assert view["outcome"]["stage_certification"] == "not_certified"
    assert view["outcome"]["scientific_decision"] == "no_go"
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
    assert view["timeline"][-1]["title"] == "Knowledge promoted"


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
    assert view["timeline"][-1]["title"] == "Capability promoted to source"
