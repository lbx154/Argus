"""Tests for experiment_audit structural gate (Step 3)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.experiment_audit_gate import (
    REPORT_BASENAME,
    REQUIRED_CHECK_KEYS,
    validate_experiment_audit,
)


def _good_audit() -> dict:
    return {
        "date": "2026-06-03",
        "auditor": "reviewer-route-xhigh",
        "overall_verdict": "warn",
        "integrity_status": "warn",
        "checks": {
            "gt_provenance":      {"status": "pass", "details": "real dataset GT"},
            "score_normalization": {"status": "warn", "details": "boundary uses self-ref"},
            "result_existence":    {"status": "pass", "details": "all files match"},
            "dead_code":           {"status": "pass", "details": "all metrics called"},
            "scope":               {"status": "warn", "details": "2 scenes only"},
            "eval_type": "real_gt",
        },
        "claims": [{"id": "C1", "impact": "supported"}],
    }


def _seed(root: Path, *, md: str | None, js: dict | None) -> None:
    paper = root / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    if md is not None:
        (paper / f"{REPORT_BASENAME}.md").write_text(md, encoding="utf-8")
    if js is not None:
        (paper / f"{REPORT_BASENAME}.json").write_text(json.dumps(js), encoding="utf-8")


def test_missing_both_files_fails(tmp_path: Path) -> None:
    report = validate_experiment_audit(tmp_path)
    codes = {i.code for i in report.issues}
    assert "missing_experiment_audit_md" in codes
    assert "missing_experiment_audit_json" in codes


def test_full_passing_audit_ok(tmp_path: Path) -> None:
    _seed(tmp_path, md="# audit\n", js=_good_audit())
    report = validate_experiment_audit(tmp_path)
    assert report.ok, report.to_text()
    assert report.integrity_status == "warn"
    assert set(report.checks_present) == set(REQUIRED_CHECK_KEYS)


def test_missing_md_with_json_present_still_fails(tmp_path: Path) -> None:
    _seed(tmp_path, md=None, js=_good_audit())
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "missing_experiment_audit_md" in codes


def test_missing_one_required_check_fails(tmp_path: Path) -> None:
    audit = _good_audit()
    del audit["checks"]["dead_code"]
    _seed(tmp_path, md="x", js=audit)
    issues = validate_experiment_audit(tmp_path).issues
    assert any(i.code == "missing_check" and "dead_code" in i.detail for i in issues)


def test_invalid_check_status_fails(tmp_path: Path) -> None:
    audit = _good_audit()
    audit["checks"]["scope"]["status"] = "maybe"
    _seed(tmp_path, md="x", js=audit)
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "invalid_check_status" in codes


def test_missing_auditor_field_fails(tmp_path: Path) -> None:
    """Anti-fab: a hand-edited audit with no auditor field would let
    an agent fake an audit by writing the JSON itself."""
    audit = _good_audit()
    audit["auditor"] = ""
    _seed(tmp_path, md="x", js=audit)
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "missing_auditor_field" in codes


def test_invalid_integrity_status_fails(tmp_path: Path) -> None:
    audit = _good_audit()
    audit["integrity_status"] = "kinda ok"
    _seed(tmp_path, md="x", js=audit)
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "invalid_integrity_status" in codes


def test_missing_eval_type_fails(tmp_path: Path) -> None:
    audit = _good_audit()
    del audit["checks"]["eval_type"]
    _seed(tmp_path, md="x", js=audit)
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "missing_eval_type" in codes


def test_malformed_json_fails(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / f"{REPORT_BASENAME}.md").write_text("x", encoding="utf-8")
    (paper / f"{REPORT_BASENAME}.json").write_text("{not json", encoding="utf-8")
    codes = {i.code for i in validate_experiment_audit(tmp_path).issues}
    assert "malformed_experiment_audit_json" in codes


# ---------------------------------------------------------------------------
# automated_gates wiring
# ---------------------------------------------------------------------------


def test_automated_gates_wires_experiment_audit_into_analysis_review_submission() -> None:
    from argus_skill.skills.automated_gates import (
        GATE_KINDS,
        STAGE_GATES,
        gates_for_stage,
    )

    for stage in ("analysis", "review", "submission"):
        assert "experiment_audit" in STAGE_GATES[stage]
        assert "experiment_audit" in gates_for_stage(stage)
    # Not at draft — at draft the agent is still writing the manuscript,
    # the audit should run alongside analysis-stage artifact rebuild.
    assert "experiment_audit" not in STAGE_GATES["draft"]
    assert GATE_KINDS["experiment_audit"] == "structural"


def test_run_stage_gates_experiment_audit_blocks_at_analysis_when_missing(tmp_path: Path) -> None:
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="analysis")
    names = {r.name for r in results}
    assert "experiment_audit" in names
    ea = next(r for r in results if r.name == "experiment_audit")
    assert ea.passed is False
    assert ea.is_blocking is True


def test_run_stage_gates_experiment_audit_passes_with_complete_artifact(tmp_path: Path) -> None:
    _seed(tmp_path, md="# audit\n", js=_good_audit())
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="analysis")
    ea = next(r for r in results if r.name == "experiment_audit")
    assert ea.passed is True, ea.detail
