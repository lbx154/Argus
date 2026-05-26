from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from paper.build_tb2_evaluation_artifacts import build_tb2_evaluation_artifacts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_tb2_evaluation_generator_writes_expected_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "paper" / "artifacts"
    outputs = build_tb2_evaluation_artifacts(out_dir)

    assert outputs["tsv"] == out_dir / "tb2_comparison.tsv"
    assert outputs["csv"] == out_dir / "tb2_comparison.csv"
    assert outputs["json"] == out_dir / "tb2_comparison.json"

    rows = list(
        csv.DictReader(
            outputs["tsv"].read_text(encoding="utf-8").splitlines(),
            delimiter="\t",
        )
    )
    assert [row["row_id"] for row in rows] == [
        "argus_v12_redux",
        "bare_gpt54",
        "argus_v12_true_023000z",
    ]
    assert rows[0]["reward"] == "0"
    assert rows[1]["reward"] == "0.662921"
    assert rows[2]["reward"] == "0.011236"
    assert rows[0]["infra_failure_kind"] == "docker_address_pool_exhaustion"
    assert rows[1]["n_errored_trials"] == "27"
    assert rows[2]["n_total_trials"] == "89"
    assert rows[2]["n_completed_trials"] == "89"
    assert rows[2]["n_errored_trials"] == "80"
    assert rows[2]["cost_usd"] == "3.55539"
    assert rows[2]["infra_failure_kind"] == "docker_compose_failure"

    for row in rows:
        for key in ("source_bundle", "evidence_1", "evidence_2", "evidence_3"):
            assert (_repo_root() / row[key]).exists()

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["artifact_name"] == "tb2_comparison"
    assert payload["artifact_version"] == 1
    assert payload["source_bundles"]["argus_v12_redux"].endswith(
        "tb2-argus-v12-redux-20260515T201322Z"
    )
    assert payload["source_bundles"]["bare_gpt54"].endswith(
        "tb2-bare-gpt54-20260515T201322Z"
    )
    assert payload["source_bundles"]["argus_v12_true_023000z"].endswith(
        "tb2-argus-v12-true-20260516T023000Z"
    )


def test_tb2_evaluation_generator_cli_produces_checked_in_artifact() -> None:
    repo = _repo_root()
    script = repo / "paper" / "build_tb2_evaluation_artifacts.py"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )

    artifact = repo / "paper" / "artifacts" / "tb2_comparison.tsv"
    assert artifact.exists()
    content = artifact.read_text(encoding="utf-8")
    assert "argus_v12_redux" in content
    assert "bare_gpt54" in content
    assert "argus_v12_true_023000z" in content
    assert "benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/summary.tsv" in content
    assert "benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv" in content
    assert "benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z/summary.tsv" in content


def test_tb2_evaluation_claim_table_paths_resolve() -> None:
    repo = _repo_root()
    claim_table = repo / "paper" / "claims_to_evidence.tsv"
    with claim_table.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    comparison = next(row for row in rows if row["claim_id"] == "tb2_comparison")
    for key in ("evidence_1", "evidence_2", "evidence_3"):
        assert comparison[key]
        assert (repo / comparison[key]).exists()

    evaluation = (repo / "paper" / "evaluation.md").read_text(encoding="utf-8")
    figures = (repo / "paper" / "figures_tables.md").read_text(encoding="utf-8")
    report = repo / "benchmarks" / "reports" / "2026-05-16-tb2-argus-v12-true-023000Z.md"
    assert "tb2_comparison.tsv" in evaluation
    assert "build_tb2_evaluation_artifacts.py" in evaluation
    assert "023000Z" in evaluation
    assert "tb2_comparison.tsv" in figures
    assert "build_tb2_evaluation_artifacts.py" in figures
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "reward=0.011236" in report_text
    assert "n_errored_trials=80" in report_text
    assert "Docker Hub pull-rate failures" in report_text
