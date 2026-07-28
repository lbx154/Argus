from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[2] / "scripts" / "mle_bench_lite" / "result_contract.py"
)
SPEC = importlib.util.spec_from_file_location("mle_result_contract", MODULE_PATH)
assert SPEC and SPEC.loader
result_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(result_contract)


def write_gate(project: Path, competition: str, submission: Path, *, satisfied: bool) -> None:
    digest = hashlib.sha256(submission.read_bytes()).hexdigest()
    (project / "MLE_MEDAL_GATE.json").write_text(
        json.dumps(
            {
                "competition": competition,
                "satisfied": satisfied,
                "submission_sha256": digest,
                "score": 0.02618,
            }
        )
    )


def build(tmp_path: Path, *, argus_exit_code: int = 127):
    submission = tmp_path / "submission.csv"
    if not submission.exists():
        submission.write_text("id,target\n1,0.5\n")
    return submission, result_contract.build_run_result(
        competition="denoising-dirty-documents",
        slot=1,
        argus_exit_code=argus_exit_code,
        grade_exit_code=0,
        submission=submission,
        grade_log=tmp_path / "grades.jsonl",
        elapsed_seconds=120,
        now=123.0,
    )


def test_current_reviewer_approved_medal_overrides_wrapper_failure(tmp_path: Path) -> None:
    submission = tmp_path / "submission.csv"
    submission.write_text("id,target\n1,0.5\n")
    write_gate(tmp_path, "denoising-dirty-documents", submission, satisfied=True)

    _, result = build(tmp_path)

    assert result["benchmark_complete"] is True
    assert result["medal_gate_satisfied"] is True
    assert result["completion_reason"] == "reviewer_approved_medal"
    assert result["retryable_infrastructure_failure"] is False


def test_stale_medal_does_not_certify_changed_submission(tmp_path: Path) -> None:
    submission = tmp_path / "submission.csv"
    submission.write_text("id,target\n1,0.5\n")
    write_gate(tmp_path, "denoising-dirty-documents", submission, satisfied=True)
    submission.write_text("id,target\n1,0.7\n")

    _, result = build(tmp_path)

    assert result["benchmark_complete"] is False
    assert result["medal_gate_satisfied"] is False
    assert result["completion_reason"] == "infrastructure_failure"


def test_clean_argus_exit_remains_a_completed_benchmark_run(tmp_path: Path) -> None:
    _, result = build(tmp_path, argus_exit_code=0)

    assert result["benchmark_complete"] is True
    assert result["medal_gate_satisfied"] is False
    assert result["completion_reason"] == "argus_clean_exit"
