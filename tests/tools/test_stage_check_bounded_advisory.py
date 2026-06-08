from __future__ import annotations

import json
import sys
from pathlib import Path

from argus_skill.tools import stage_check


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload))


def _seed_plan_shell_files(root: Path) -> None:
    for rel in (
        "research/EXPERIMENT_PLAN.md",
        "research/IDEA_REJECTION_LOG.md",
        "research/CODE_STUDY_NOTES.md",
        "research/BASELINE_AND_BENCHMARK_PLAN.md",
    ):
        _write(root / rel)


def _seed_blocked_pipeline(root: Path) -> None:
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "status": "blocked",
            "last_gate": {"reason": "official benchmark artifacts missing"},
            "stages": {"plan": {"status": "blocked", "reason": "external data"}},
        },
    )


def _seed_benchmark_external_block(root: Path) -> None:
    _write_json(
        root / "experiments" / "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        {
            "passed": False,
            "blockers": [
                {
                    "family_id": "official_compbench20",
                    "id": "artifact.missing_raw_artifact",
                    "message": "official rows absent",
                }
            ],
        },
    )


def _run_stage_check(root: Path, *, bounded: bool, stage: str = "plan", monkeypatch=None) -> int:
    argv = ["stage-check", "--project-root", str(root), "--stage", stage]
    if bounded:
        argv.append("--bounded")
    if monkeypatch is None:
        raise AssertionError("pass pytest monkeypatch fixture")
    monkeypatch.setattr(sys, "argv", argv)
    return stage_check.main()


def test_pipeline_blocked_fails_closed_when_not_bounded(tmp_path: Path, monkeypatch, capsys):
    _seed_plan_shell_files(tmp_path)
    _seed_blocked_pipeline(tmp_path)

    status = _run_stage_check(tmp_path, bounded=False, monkeypatch=monkeypatch)
    out = capsys.readouterr().out

    assert status == 1
    assert "Fail-closed pipeline state" in out
    assert "❌ pipeline status is blocked" in out
    assert "fail-closed state finding(s)" in out


def test_pipeline_blocked_is_advisory_when_bounded(tmp_path: Path, monkeypatch, capsys):
    _seed_plan_shell_files(tmp_path)
    _seed_blocked_pipeline(tmp_path)

    status = _run_stage_check(tmp_path, bounded=True, monkeypatch=monkeypatch)
    out = capsys.readouterr().out

    assert status == 0
    assert "Advisory paper-pipeline state" in out
    assert "📋 pipeline status is blocked" in out
    assert "0 fail-closed state finding(s)" in out
    assert "advisory finding(s)" in out


def test_benchmark_external_block_is_advisory_when_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _seed_plan_shell_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "plan", "status": "active"},
    )
    _seed_benchmark_external_block(tmp_path)

    status = _run_stage_check(tmp_path, bounded=True, monkeypatch=monkeypatch)
    out = capsys.readouterr().out

    assert status == 0
    assert "📋 benchmark artifact bundle is blocked" in out
    assert "0 fail-closed state finding(s)" in out


def test_benchmark_external_block_fails_when_not_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _seed_plan_shell_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "plan", "status": "active"},
    )
    _seed_benchmark_external_block(tmp_path)

    status = _run_stage_check(tmp_path, bounded=False, monkeypatch=monkeypatch)
    out = capsys.readouterr().out

    assert status == 1
    assert "❌ benchmark artifact bundle is blocked" in out


def test_structural_antifraud_gate_still_blocks_when_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "analysis", "status": "active"},
    )
    _write(tmp_path / "paper" / "RESULTS_REPORT.md")
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "x\n")
    _write(tmp_path / "paper" / "figures" / "fig.png", "not really a png\n")

    status = _run_stage_check(tmp_path, bounded=True, stage="analysis", monkeypatch=monkeypatch)
    out = capsys.readouterr().out

    assert status == 1
    assert "evidence_chain (structural)" in out
    assert "structural-gate fail" in out
