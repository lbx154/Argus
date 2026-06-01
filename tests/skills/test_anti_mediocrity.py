"""Tests for argus_skill.skills.anti_mediocrity (F3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.anti_mediocrity import (
    AggregateRow,
    MediocrityIssue,
    check_baseline_reproduced,
    check_benchmark_diversity,
    check_improvement_threshold,
    main as anti_mediocrity_main,
    run_anti_mediocrity_gate,
)


# ---------------------------------------------------------------------------
# unit-level tests for the three pure validator functions
# ---------------------------------------------------------------------------


def _agg(condition: str, reward: float, *, total: int = 89, errored: int = 0) -> AggregateRow:
    return AggregateRow(
        bundle=f"benchmarks/evidence/{condition}",
        condition=condition,
        reward=reward,
        n_total_trials=total,
        n_completed_trials=total - errored,
        n_errored_trials=errored,
    )


def test_baseline_reproduced_pass() -> None:
    issues = check_baseline_reproduced([_agg("bare-gpt54", 0.66)], "bare-gpt54")
    assert issues == []


def test_baseline_reproduced_fails_when_missing() -> None:
    issues = check_baseline_reproduced([_agg("argus", 0.7)], "bare-gpt54")
    codes = {i.code for i in issues}
    assert "baseline_not_reproduced" in codes


def test_baseline_reproduced_fails_when_all_zero_reward() -> None:
    issues = check_baseline_reproduced([_agg("bare-gpt54", 0.0)], "bare-gpt54")
    codes = {i.code for i in issues}
    assert "baseline_zero_reward" in codes


def test_baseline_dirty_bundle_does_not_count() -> None:
    # 50% errored → not clean enough → treated as if baseline is missing.
    dirty = _agg("bare-gpt54", 0.66, total=10, errored=5)
    issues = check_baseline_reproduced([dirty], "bare-gpt54")
    codes = {i.code for i in issues}
    assert "baseline_not_reproduced" in codes


def test_improvement_above_threshold_passes() -> None:
    aggs = [_agg("argus", 0.70), _agg("bare-gpt54", 0.60)]
    issues = check_improvement_threshold(aggs, "argus", "bare-gpt54", min_delta=0.02)
    assert issues == []


def test_improvement_below_threshold_flagged() -> None:
    aggs = [_agg("argus", 0.605), _agg("bare-gpt54", 0.60)]
    issues = check_improvement_threshold(aggs, "argus", "bare-gpt54", min_delta=0.02)
    assert len(issues) == 1
    assert issues[0].code == "improvement_below_threshold"
    assert issues[0].measured == pytest.approx(0.005, abs=1e-6)


def test_improvement_negative_flagged() -> None:
    aggs = [_agg("argus", 0.50), _agg("bare-gpt54", 0.66)]
    issues = check_improvement_threshold(aggs, "argus", "bare-gpt54")
    assert len(issues) == 1
    assert issues[0].code == "improvement_below_threshold"


def test_improvement_missing_proposed_flagged() -> None:
    aggs = [_agg("bare-gpt54", 0.66)]
    issues = check_improvement_threshold(aggs, "argus", "bare-gpt54")
    codes = {i.code for i in issues}
    assert "proposed_missing" in codes


def test_benchmark_diversity_pass() -> None:
    issues = check_benchmark_diversity(
        {"terminal-bench@2.0", "swebench-pro@1.0", "mlagentbench@1.1"},
        min_families=3,
    )
    assert issues == []


def test_benchmark_diversity_insufficient_flagged() -> None:
    issues = check_benchmark_diversity({"terminal-bench@2.0"}, min_families=3)
    assert len(issues) == 1
    assert issues[0].code == "benchmark_diversity_insufficient"
    assert issues[0].measured == 1
    assert issues[0].threshold == 3


# ---------------------------------------------------------------------------
# integration: run_anti_mediocrity_gate against a fake project tree
# ---------------------------------------------------------------------------


def _write_bundle(
    root: Path,
    name: str,
    *,
    condition: str,
    reward: float,
    dataset_id: str,
    total: int = 89,
    errored: int = 0,
) -> None:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    header = (
        "row_kind\tcondition\treward\tn_total_trials\t"
        "n_completed_trials\tn_errored_trials\n"
    )
    body = (
        f"aggregate\t{condition}\t{reward}\t{total}\t{total - errored}\t{errored}\n"
    )
    (bundle / "summary.tsv").write_text(header + body, encoding="utf-8")
    (bundle / "BUILD_INFO.md").write_text("# Build Info\n", encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"dataset_id": dataset_id, "condition": condition}),
        encoding="utf-8",
    )


def test_run_gate_full_pass(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72, dataset_id="terminal-bench@2.0")
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60, dataset_id="terminal-bench@2.0")
    _write_bundle(tmp_path, "swe-bundle", condition="argus", reward=0.45, dataset_id="swebench-pro@1.0")
    _write_bundle(tmp_path, "mla-bundle", condition="argus", reward=0.50, dataset_id="mlagentbench@1.1")

    report = run_anti_mediocrity_gate(
        tmp_path,
        proposed_condition="argus",
        baseline_condition="bare",
        min_delta=0.02,
        min_families=3,
    )

    assert report.ok, [(i.code, i.detail) for i in report.issues]
    assert len(report.aggregates) == 4


def test_run_gate_fails_on_insufficient_delta(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.605, dataset_id="terminal-bench@2.0")
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.600, dataset_id="terminal-bench@2.0")
    _write_bundle(tmp_path, "swe-bundle", condition="argus", reward=0.45, dataset_id="swebench-pro@1.0")
    _write_bundle(tmp_path, "mla-bundle", condition="argus", reward=0.50, dataset_id="mlagentbench@1.1")

    report = run_anti_mediocrity_gate(
        tmp_path,
        proposed_condition="argus",
        baseline_condition="bare",
        min_delta=0.02,
        min_families=3,
    )

    codes = {i.code for i in report.issues}
    assert "improvement_below_threshold" in codes


def test_run_gate_fails_on_insufficient_benchmark_diversity(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72, dataset_id="terminal-bench@2.0")
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60, dataset_id="terminal-bench@2.0")

    report = run_anti_mediocrity_gate(
        tmp_path,
        proposed_condition="argus",
        baseline_condition="bare",
        min_delta=0.02,
        min_families=3,
    )

    codes = {i.code for i in report.issues}
    assert "benchmark_diversity_insufficient" in codes


def test_cli_exits_nonzero_on_issues(tmp_path: Path, capsys) -> None:
    _write_bundle(tmp_path, "single-bundle", condition="argus", reward=0.5, dataset_id="terminal-bench@2.0")

    rc = anti_mediocrity_main(
        [
            "--project-root",
            str(tmp_path),
            "--proposed-condition",
            "argus",
            "--baseline-condition",
            "bare",
            "--min-benchmark-families",
            "3",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "baseline_not_reproduced" in out
    assert "benchmark_diversity_insufficient" in out


def test_cli_json_report(tmp_path: Path, capsys) -> None:
    _write_bundle(tmp_path, "single-bundle", condition="argus", reward=0.5, dataset_id="terminal-bench@2.0")

    rc = anti_mediocrity_main(
        [
            "--project-root",
            str(tmp_path),
            "--proposed-condition",
            "argus",
            "--baseline-condition",
            "bare",
            "--min-benchmark-families",
            "3",
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["issue_count"] >= 2
    assert any(i["code"] == "baseline_not_reproduced" for i in payload["issues"])
