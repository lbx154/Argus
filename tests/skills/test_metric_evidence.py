from __future__ import annotations

import csv

import pytest

from argus.verticals.metric_evidence import (
    EvidenceError,
    validate_kernelbench_evidence,
    validate_nanogpt_evidence,
    validate_speedrun_evidence,
    validate_speedrun_reference,
)


def _csv(path, fieldnames, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_speedrun_requires_a_finite_metric_row(tmp_path):
    notes = tmp_path / "experiments" / "notes.md"
    notes.parent.mkdir()
    notes.write_text("MEAN_VAL_BPB and seconds_to_target are discussed here", encoding="utf-8")
    _csv(
        tmp_path / "attempts" / "a" / "results.csv",
        ["seed", "comment"],
        [{"seed": "1", "comment": "val_bpb pending"}],
    )
    with pytest.raises(EvidenceError):
        validate_speedrun_evidence(tmp_path)

    result = tmp_path / "attempts" / "a" / "results.csv"
    _csv(result, ["seed", "val_bpb"], [{"seed": "1", "val_bpb": "3.28"}])
    assert validate_speedrun_evidence(tmp_path) == result


def test_speedrun_reference_requires_structured_metric(tmp_path):
    markdown = tmp_path / "research" / "GROUND_TRUTH.md"
    markdown.parent.mkdir()
    markdown.write_text("val_bpb = 3.28", encoding="utf-8")
    with pytest.raises(EvidenceError):
        validate_speedrun_reference(tmp_path)

    reference = tmp_path / "research" / "REFERENCE_SCORES.json"
    reference.write_text('{"metrics": {"val_bpb": 3.28}}', encoding="utf-8")
    assert validate_speedrun_reference(tmp_path) == reference


def test_nanogpt_requires_target_loss_and_8xh100_timing(tmp_path):
    result = tmp_path / "attempts" / "a" / "results.csv"
    _csv(result, ["val_bpb"], [{"val_bpb": "3.28"}])
    with pytest.raises(EvidenceError):
        validate_nanogpt_evidence(tmp_path)

    _csv(result, ["seconds_to_target"], [{"seconds_to_target": "77.3"}])
    with pytest.raises(EvidenceError):
        validate_nanogpt_evidence(tmp_path)

    _csv(
        result,
        ["seconds_to_target", "val_loss", "gpu_count", "gpu_model"],
        [{
            "seconds_to_target": "77.3",
            "val_loss": "3.28",
            "gpu_count": "8",
            "gpu_model": "NVIDIA H100 80GB HBM3",
        }],
    )
    assert validate_nanogpt_evidence(tmp_path) == result


def test_kernelbench_requires_correctness_gated_sol(tmp_path):
    result = tmp_path / "experiments" / "kernel" / "scores.csv"
    _csv(result, ["correct", "sol_pct"], [{"correct": "false", "sol_pct": "98.2"}])
    with pytest.raises(EvidenceError):
        validate_kernelbench_evidence(tmp_path)

    _csv(result, ["correct", "sol_pct"], [{"correct": "true", "sol_pct": "98.2"}])
    assert validate_kernelbench_evidence(tmp_path) == result


def test_kernelbench_accepts_structured_json_result(tmp_path):
    result = tmp_path / "attempts" / "kernel" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"correct": true, "sol_pct": 91.5}', encoding="utf-8")
    assert validate_kernelbench_evidence(tmp_path) == result


def test_kernelbench_rejects_conflicting_sol_fields(tmp_path):
    result = tmp_path / "attempts" / "kernel" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text(
        '{"correct": true, "sol_pct": 91.5, "sol": -1}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError):
        validate_kernelbench_evidence(tmp_path)

    result.write_text(
        '{"correct": true, "sol_pct": 91.5, "sol": "NaN"}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError):
        validate_kernelbench_evidence(tmp_path)
