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


def test_structured_events_build_reviewer_certified_achievement(tmp_path: Path) -> None:
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
    )
    emit(
        tmp_path,
        "skill.created",
        10,
        skill_id="skill-1",
        name="fused-epilogue-playbook",
        version=1,
        scope="engineer",
        path="skills/fused-epilogue-playbook.md",
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
    view = emit(
        tmp_path,
        "life.mission.completed",
        12,
        item_id="task-1",
        title="Profile fused kernel",
        objective="Optimize FlashAttention on B200",
        status="done",
        success=True,
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
    assert load_mission_view(tmp_path)["achievement"] == view["achievement"]


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

    assert view["storage"] == {
        "project_skill_dir": "/state/project/skills",
        "global_skill_dir": "/state/global/skills",
        "project_skill_count": 3,
        "global_skill_count": 20,
        "wiki_paths": ["/workspace/.autors/demo/wiki"],
    }
