from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.manager import Manager


class _ExplodingRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise AssertionError("manager LLM runner should not be called")


class _Review:
    status = "continue"
    reason = "downstream work is blocked by accepted rollback packet"
    checklist = [
        {
            "item": "submission.live_manager_rollback_consumption",
            "satisfied": False,
        }
    ]
    planner_report = {"forward_progress": False}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_project(root: Path, *, current_stage: str = "submission") -> None:
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": current_stage,
            "stages": {
                "research": {"status": "done"},
                "plan": {"status": "done"},
                "benchmark": {"status": "done"},
                "run": {"status": "done"},
                "analysis": {"status": "done"},
                "draft": {"status": "done"},
                "review": {"status": "done"},
                "submission": {"status": "pending"},
            },
        },
    )


def _seed_evidence_files(root: Path) -> dict[str, str]:
    files = {
        "analysis_route_decision": "paper/ANALYSIS_ROUTE_DECISION.json",
        "evidence_bundle": "experiments/run_stage/EVIDENCE_BUNDLE.json",
        "manager_action_request": "research/MANAGER_ACTION_REQUEST.json",
        "pipeline_state": "research/PIPELINE_STATE.json",
        "run_stage_routing_request": "experiments/run_stage/RUN_STAGE_ROUTING_REQUEST.json",
    }
    for key, rel in files.items():
        if key == "pipeline_state":
            continue
        _write_json(root / rel, {"ok": True})
    return files


def _seed_artifact(root: Path, **overrides) -> dict:
    payload = {
        "outcome": "MANAGER_BLOCKED",
        "status": "rollback-accepted",
        "requested_stage": "submission",
        "current_stage": "submission",
        "earliest_broken_stage": "run",
        "rollback_target": "run",
        "manager_action_required": "rollback_stage_to_run",
        "pipeline_stage_fields_clean": True,
        "evidence_files": _seed_evidence_files(root),
    }
    payload.update(overrides)
    _write_json(root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json", payload)
    return payload


def _read_stage(root: Path) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["current_stage"]


def test_manager_consumes_valid_blocked_rollback_artifact(tmp_path: Path) -> None:
    _seed_project(tmp_path)
    _seed_artifact(tmp_path)

    transition = Manager(project_root=tmp_path, runner=None).decide_stage_transition(
        review=None,
        project_root=tmp_path,
    )

    assert transition.action == "rollback"
    assert transition.target_stage == "run"
    assert transition.source == "manager_blocked_rollback_artifact"
    assert transition.diagnostic == "accepted_manager_blocked_artifact"
    assert _read_stage(tmp_path) == "run"


def test_manager_consumes_valid_artifact_before_llm_even_with_review(
    tmp_path: Path,
) -> None:
    _seed_project(tmp_path)
    _seed_artifact(tmp_path)

    transition = Manager(
        project_root=tmp_path,
        runner=_ExplodingRunner(),
    ).decide_stage_transition(
        review=_Review(),
        project_root=tmp_path,
    )

    assert transition.action == "rollback"
    assert transition.target_stage == "run"
    assert transition.source == "manager_blocked_rollback_artifact"
    assert transition.diagnostic == "accepted_manager_blocked_artifact"
    assert _read_stage(tmp_path) == "run"


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "blocked"},
        {"requested_stage": "review"},
        {"current_stage": "review"},
        {"rollback_target": "submission", "earliest_broken_stage": "submission", "manager_action_required": "rollback_stage_to_submission"},
        {"pipeline_stage_fields_clean": False},
    ],
)
def test_manager_rejects_invalid_or_stale_blocked_artifacts(
    tmp_path: Path,
    overrides: dict,
) -> None:
    _seed_project(tmp_path)
    _seed_artifact(tmp_path, **overrides)

    transition = Manager(project_root=tmp_path, runner=None).decide_stage_transition(
        review=None,
        project_root=tmp_path,
    )

    assert transition.action == "hold"
    assert transition.source == "no_review_hold"
    assert _read_stage(tmp_path) == "submission"


def test_manager_rejects_artifact_with_missing_evidence_file(tmp_path: Path) -> None:
    _seed_project(tmp_path)
    files = _seed_evidence_files(tmp_path)
    (tmp_path / files["evidence_bundle"]).unlink()
    _write_json(
        tmp_path / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json",
        {
            "outcome": "MANAGER_BLOCKED",
            "status": "rollback-accepted",
            "requested_stage": "submission",
            "current_stage": "submission",
            "earliest_broken_stage": "run",
            "rollback_target": "run",
            "manager_action_required": "rollback_stage_to_run",
            "pipeline_stage_fields_clean": True,
            "evidence_files": files,
        },
    )

    transition = Manager(project_root=tmp_path, runner=None).decide_stage_transition(
        review=None,
        project_root=tmp_path,
    )

    assert transition.action == "hold"
    assert transition.source == "no_review_hold"
    assert _read_stage(tmp_path) == "submission"
