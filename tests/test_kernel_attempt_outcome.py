from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.kernel_engineering.attempt_outcome import (
    main,
    validate_outcome,
)


def _outcome(**updates) -> dict:
    record = {
        "schema_version": 1,
        "attempt_id": "a001-tilelang",
        "execution_status": "completed",
        "failure_class": "none",
        "idea_status": "supported",
        "baseline_identity": "main@abc123 in env sha256:base",
        "candidate_identity": "feature@abc123+dirty sha256:diff",
        "path_coverage": "dispatch log shows the candidate backend handled shape S1",
        "summary": "Candidate passed and improved the measured path.",
        "evidence": "attempts/a001/result.json",
    }
    record.update(updates)
    return record


def test_environment_failure_cannot_refute_idea() -> None:
    bad = _outcome(
        execution_status="blocked",
        failure_class="environment",
        idea_status="refuted",
        summary="TileLang import failed because the package was absent.",
    )
    errors = validate_outcome(bad)
    assert any("environment failure" in error for error in errors)
    assert any("cannot support or refute" in error for error in errors)


def test_environment_failure_keeps_idea_untested() -> None:
    record = _outcome(
        execution_status="blocked",
        failure_class="toolchain",
        idea_status="untested",
        summary="NVCC was unavailable, so the candidate was not compiled.",
    )
    assert validate_outcome(record) == []


def test_valid_performance_result_can_refute_idea() -> None:
    record = _outcome(
        execution_status="completed",
        failure_class="performance",
        idea_status="refuted",
        summary="Correct isolated benchmark regressed beyond noise.",
    )
    assert validate_outcome(record) == []


def test_performance_claim_requires_changed_path_and_diff_identity() -> None:
    record = _outcome(
        candidate_identity="",
        path_coverage="",
    )
    errors = validate_outcome(record)
    assert any("candidate_identity" in error for error in errors)
    assert any("path_coverage" in error for error in errors)


def test_project_check_validates_outcome_files(tmp_path: Path) -> None:
    path = tmp_path / "attempts" / "a001" / "OUTCOME.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_outcome()), encoding="utf-8")
    assert main(["check", "--project-root", str(tmp_path)]) == 0


def test_project_check_rejects_missing_outcomes(tmp_path: Path) -> None:
    assert main(["check", "--project-root", str(tmp_path)]) == 2
