"""Integration tests for F3/F4/F5 wiring.

Verifies that:

* ``argus_skill.skills.automated_gates`` correctly maps stages → gates and
  produces ``GateResult`` from the underlying F3/F4 validators.
* ``argus_skill.tools.stage_check`` runs the automated gates after its own
  shell checks (so the daemon's default ``check_commands`` picks them up
  every engineer round).
* The new ``argus-skill --evidence-chain-check`` /
  ``--anti-mediocrity-check`` / ``--lifecycle-status`` CLI flags dispatch
  correctly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.skills.automated_gates import (
    GateResult,
    STAGE_GATES,
    all_passed,
    format_results,
    gates_for_stage,
    main as automated_gates_main,
    run_stage_gates,
)


# ---------------------------------------------------------------------------
# helpers — minimal project skeleton with bundles and a claims TSV
# ---------------------------------------------------------------------------


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _write_bundle(
    root: Path,
    name: str,
    *,
    condition: str = "argus",
    reward: float = 0.7,
    dataset_id: str = "terminal-bench@2.0",
    total: int = 89,
    errored: int = 0,
    tainted: bool = False,
) -> Path:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    summary_header = (
        "row_kind\tcondition\treward\tn_total_trials\t"
        "n_completed_trials\tn_errored_trials\n"
    )
    summary_body = (
        f"aggregate\t{condition}\t{reward}\t{total}\t{total - errored}\t{errored}\n"
    )
    (bundle / "summary.tsv").write_text(summary_header + summary_body, encoding="utf-8")
    build_info = "# Build Info\n- status: completed\n"
    if tainted:
        build_info += "TAINTED — DO NOT CITE AS PERFORMANCE.\n"
    (bundle / "BUILD_INFO.md").write_text(build_info, encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"dataset_id": dataset_id, "condition": condition}),
        encoding="utf-8",
    )
    return bundle


def _write_claims_tsv(root: Path, rows: list[dict[str, str]]) -> Path:
    cols = [
        "claim_id", "status", "claim",
        "evidence_1", "evidence_2", "evidence_3", "notes",
    ]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))
    path = root / "paper" / "claims_to_evidence.tsv"
    _write(path, "\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# STAGE_GATES map
# ---------------------------------------------------------------------------


def test_stage_gates_map_covers_canonical_stages() -> None:
    for stage in (
        "research", "plan", "benchmark",
        "run", "analysis", "draft", "review", "submission",
    ):
        # Every canonical stage is present in the map (even if it maps to ())
        assert stage in STAGE_GATES


def test_research_and_plan_have_no_automated_gates() -> None:
    # These stages produce no evidence yet, so automated gates are skipped
    # (the reviewer is the only check).
    assert gates_for_stage("research") == ()
    assert gates_for_stage("plan") == ()
    assert gates_for_stage("benchmark") == ()


def test_review_and_submission_run_both_gates() -> None:
    for stage in ("review", "submission"):
        gates = set(gates_for_stage(stage))
        assert "evidence_chain" in gates
        assert "anti_mediocrity" in gates


def test_run_stage_only_runs_anti_mediocrity() -> None:
    assert gates_for_stage("run") == ("anti_mediocrity",)


def test_draft_only_runs_evidence_chain() -> None:
    assert gates_for_stage("draft") == ("evidence_chain",)


def test_unknown_stage_returns_empty() -> None:
    assert gates_for_stage("nonsense") == ()


# ---------------------------------------------------------------------------
# run_stage_gates: end-to-end via fake project
# ---------------------------------------------------------------------------


def test_run_stage_gates_review_clean_project_passes(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60)
    _write_bundle(
        tmp_path, "swe-bundle",
        condition="argus", reward=0.5, dataset_id="swebench-pro@1.0",
    )
    _write_bundle(
        tmp_path, "mla-bundle",
        condition="argus", reward=0.55, dataset_id="mlagentbench@1.1",
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "Argus beats bare on TB2",
                "evidence_1": "benchmarks/evidence/argus-bundle/summary.tsv",
                "evidence_2": "benchmarks/evidence/bare-bundle/summary.tsv",
            }
        ],
    )

    results = run_stage_gates(
        tmp_path,
        stage="review",
        proposed_condition="argus",
        baseline_condition="bare",
    )

    assert len(results) == 2
    names = [r.name for r in results]
    assert names == ["evidence_chain", "anti_mediocrity"]
    assert all_passed(results), [(r.name, r.detail) for r in results]


def test_run_stage_gates_surfaces_evidence_chain_break(tmp_path: Path) -> None:
    # Cite a path that does not exist.
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/missing/summary.tsv",
            }
        ],
    )

    results = run_stage_gates(tmp_path, stage="draft")

    assert len(results) == 1
    assert results[0].name == "evidence_chain"
    assert not results[0].passed
    assert "evidence_path_missing" in results[0].detail


def test_run_stage_gates_surfaces_baseline_not_reproduced(tmp_path: Path) -> None:
    # Provide proposed but no baseline aggregate.
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)
    _write_bundle(
        tmp_path, "swe-bundle",
        condition="argus", reward=0.5, dataset_id="swebench-pro@1.0",
    )
    _write_bundle(
        tmp_path, "mla-bundle",
        condition="argus", reward=0.55, dataset_id="mlagentbench@1.1",
    )

    results = run_stage_gates(
        tmp_path,
        stage="run",
        proposed_condition="argus",
        baseline_condition="bare",
    )

    assert len(results) == 1
    assert results[0].name == "anti_mediocrity"
    assert not results[0].passed
    assert "baseline_not_reproduced" in results[0].detail


# ---------------------------------------------------------------------------
# format_results — reviewer-facing string
# ---------------------------------------------------------------------------


def test_format_results_empty_returns_empty_string() -> None:
    assert format_results([]) == ""


def test_format_results_includes_pass_and_fail_blocks() -> None:
    blocks = format_results(
        [
            GateResult(name="evidence_chain", passed=True, summary="clean", detail=""),
            GateResult(name="anti_mediocrity", passed=False, summary="2 fails", detail="details here"),
        ]
    )
    assert "[PASS] gate:evidence_chain" in blocks
    assert "[FAIL] gate:anti_mediocrity" in blocks
    assert "details here" in blocks


# ---------------------------------------------------------------------------
# CLI entry — automated_gates module
# ---------------------------------------------------------------------------


def test_automated_gates_cli_exits_zero_on_clean(tmp_path: Path, capsys) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60)
    _write_bundle(
        tmp_path, "swe-bundle",
        condition="argus", reward=0.5, dataset_id="swebench-pro@1.0",
    )
    _write_bundle(
        tmp_path, "mla-bundle",
        condition="argus", reward=0.55, dataset_id="mlagentbench@1.1",
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/argus-bundle/summary.tsv",
            }
        ],
    )

    rc = automated_gates_main(
        [
            "--project-root", str(tmp_path),
            "--stage", "review",
            "--proposed-condition", "argus",
            "--baseline-condition", "bare",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[PASS] gate:evidence_chain" in out
    assert "[PASS] gate:anti_mediocrity" in out


def test_automated_gates_cli_research_stage_no_gates(tmp_path: Path, capsys) -> None:
    rc = automated_gates_main(
        ["--project-root", str(tmp_path), "--stage", "research"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "No automated gates configured" in out


def test_automated_gates_cli_json_payload(tmp_path: Path, capsys) -> None:
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/missing/summary.tsv",
            }
        ],
    )

    rc = automated_gates_main(
        ["--project-root", str(tmp_path), "--stage", "draft", "--json"]
    )
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["stage"] == "draft"
    assert payload["all_passed"] is False
    assert any(r["name"] == "evidence_chain" for r in payload["results"])


# ---------------------------------------------------------------------------
# stage_check.py — daemon's default check_commands target
# ---------------------------------------------------------------------------


def test_stage_check_runs_automated_gates_for_review_stage(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    # Set up a project root with the minimal stage_check shell-check assets +
    # a clean evidence chain.
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "review"}), encoding="utf-8"
    )
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)
    _write_bundle(tmp_path, "bare-bundle", condition="bare", reward=0.60)
    _write_bundle(
        tmp_path, "swe-bundle",
        condition="argus", reward=0.5, dataset_id="swebench-pro@1.0",
    )
    _write_bundle(
        tmp_path, "mla-bundle",
        condition="argus", reward=0.55, dataset_id="mlagentbench@1.1",
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/argus-bundle/summary.tsv",
            }
        ],
    )
    monkeypatch.setenv("ARGUS_SKILL_PROPOSED_CONDITION", "argus")
    monkeypatch.setenv("ARGUS_SKILL_BASELINE_CONDITION", "bare")

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.stage_check",
         "--project-root", str(tmp_path), "--stage", "review"],
        text=True,
        capture_output=True,
    )

    # Shell checks at the review stage will likely fail (no review JSONs),
    # but the automated gates should appear in stdout and the gates we
    # care about should be marked PASS.
    assert "Automated gates for stage 'review'" in proc.stdout
    assert "evidence_chain" in proc.stdout
    assert "anti_mediocrity" in proc.stdout
    # Both gates pass on the clean fixture.
    assert "✅ evidence_chain" in proc.stdout
    assert "✅ anti_mediocrity" in proc.stdout


def test_stage_check_surfaces_evidence_chain_break(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "draft"}), encoding="utf-8"
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/missing/summary.tsv",
            }
        ],
    )

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.stage_check",
         "--project-root", str(tmp_path), "--stage", "draft"],
        text=True,
        capture_output=True,
    )

    assert "❌ evidence_chain" in proc.stdout
    assert "evidence_path_missing" in proc.stdout
    # Non-zero exit because the gate failed.
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Top-level argus-skill CLI — F5 lifecycle status flag
# ---------------------------------------------------------------------------


def test_cli_lifecycle_status_on_minimal_project(tmp_path: Path) -> None:
    # A project root with no evidence and no draft → state should be
    # incubating (until the 7-day timeout fires, but tmp_path is fresh).
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-status", "--project-root", str(tmp_path)],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "project lifecycle (F5)" in proc.stdout
    assert "observed_state    : incubating" in proc.stdout
    assert "token_allocatable : True" in proc.stdout


def test_cli_lifecycle_status_running_when_evidence_exists(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle")

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--lifecycle-status", "--project-root", str(tmp_path)],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "observed_state    : running" in proc.stdout


def test_cli_evidence_chain_check_passes_on_clean(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle")
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "demo",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/argus-bundle/summary.tsv",
            }
        ],
    )

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--evidence-chain-check", "--project-root", str(tmp_path)],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_cli_anti_mediocrity_check_fails_without_baseline(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "argus-bundle", condition="argus", reward=0.72)

    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill",
         "--anti-mediocrity-check",
         "--project-root", str(tmp_path),
         "--proposed-condition", "argus",
         "--baseline-condition", "bare"],
        text=True,
        capture_output=True,
    )

    assert proc.returncode != 0
    assert "baseline_not_reproduced" in proc.stdout
