from __future__ import annotations

import json
import sys
from pathlib import Path

from argus_skill.tools import stage_check


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_submission_project(root: Path) -> None:
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "submission",
            "stages": {
                "run": {"status": "done"},
                "analysis": {"status": "done"},
                "draft": {"status": "done"},
                "review": {"status": "done"},
                "submission": {"status": "pending"},
            },
        },
    )


def _seed_positive_rollback_evidence(root: Path) -> None:
    _write_json(
        root / "paper" / "ANALYSIS_ROUTE_DECISION.json",
        {
            "earliest_broken_stage": "run",
            "engineer_modified_pipeline_stage_fields": False,
        },
    )
    _write_json(
        root / "research" / "MANAGER_ACTION_REQUEST.json",
        {
            "requested_action": "rollback_stage_to_run",
            "earliest_broken_stage": "run",
            "engineer_modified_pipeline_stage_fields": False,
        },
    )
    _write_json(
        root / "experiments" / "run_stage" / "EVIDENCE_BUNDLE.json",
        {
            "row_count": 184,
            "paper_evidence_allowed_values": [False],
            "full_scale_blockers": ["No full-scale matrix has been executed."],
        },
    )
    _write_json(
        root / "experiments" / "run_stage" / "RUN_STAGE_ROUTING_REQUEST.json",
        {"current_stage": "draft"},
    )


def _seed_existing_manager_blocked_packet(root: Path) -> None:
    _write_json(
        root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json",
        {
            "outcome": "MANAGER_BLOCKED",
            "status": "rollback-accepted",
            "requested_stage": "submission",
            "current_stage": "submission",
            "earliest_broken_stage": "run",
            "rollback_target": "run",
            "manager_action_required": "rollback_stage_to_run",
            "pipeline_stage_fields_clean": True,
            "evidence_files": {
                "analysis_route_decision": "paper/ANALYSIS_ROUTE_DECISION.json",
                "evidence_bundle": "experiments/run_stage/EVIDENCE_BUNDLE.json",
                "manager_action_request": "research/MANAGER_ACTION_REQUEST.json",
                "pipeline_state": "research/PIPELINE_STATE.json",
                "run_stage_routing_request": "experiments/run_stage/RUN_STAGE_ROUTING_REQUEST.json",
            },
        },
    )


def test_bounded_submission_accepts_positive_rollback_packet(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_submission_project(tmp_path)
    _seed_positive_rollback_evidence(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--stage",
            "submission",
            "--bounded",
            "--vertical",
            "research",
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out
    packet = json.loads(
        (tmp_path / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json").read_text(
            encoding="utf-8"
        )
    )

    assert status == 0
    assert "MANAGER_BLOCKED / rollback-accepted" in out
    assert "paper/main.tex" not in out
    assert packet["outcome"] == "MANAGER_BLOCKED"
    assert packet["status"] == "rollback-accepted"
    assert packet["requested_stage"] == "submission"
    assert packet["current_stage"] == "submission"
    assert packet["earliest_broken_stage"] == "run"
    assert packet["rollback_target"] == "run"
    assert packet["manager_action_required"] == "rollback_stage_to_run"
    assert packet["pipeline_stage_fields_clean"] is True


def test_default_submission_accepts_existing_positive_rollback_packet(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_submission_project(tmp_path)
    _seed_positive_rollback_evidence(tmp_path)
    _seed_existing_manager_blocked_packet(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out
    packet = json.loads(
        (tmp_path / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json").read_text(
            encoding="utf-8"
        )
    )

    assert status == 0
    assert "MANAGER_BLOCKED / rollback-accepted" in out
    assert "paper/main.tex" not in out
    assert packet["requested_stage"] == "submission"
    assert packet["rollback_target"] == "run"


def test_bounded_submission_without_rollback_evidence_keeps_ordinary_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_submission_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--stage",
            "submission",
            "--bounded",
            "--vertical",
            "research",
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "MANAGER_BLOCKED / rollback-accepted" not in out
    assert "paper/main.tex" in out
    assert not (tmp_path / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json").exists()


def test_default_submission_without_rollback_evidence_keeps_ordinary_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_submission_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
        ],
    )

    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "MANAGER_BLOCKED / rollback-accepted" not in out
    assert "paper/main.tex" in out
    assert not (tmp_path / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json").exists()


def test_stage_check_acceptance_does_not_edit_pipeline_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_submission_project(tmp_path)
    _seed_positive_rollback_evidence(tmp_path)
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    before = state_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage-check",
            "--project-root",
            str(tmp_path),
            "--stage",
            "submission",
            "--bounded",
            "--vertical",
            "research",
        ],
    )

    assert stage_check.main() == 0
    capsys.readouterr()
    assert state_path.read_text(encoding="utf-8") == before
