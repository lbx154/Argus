from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from paper.build_user_study_artifacts import build_user_study_artifacts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_user_study_generator_writes_expected_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "paper" / "artifacts"
    outputs = build_user_study_artifacts(out_dir)

    assert outputs["tsv"] == out_dir / "user_study_metrics.tsv"
    assert outputs["csv"] == out_dir / "user_study_metrics.csv"
    assert outputs["json"] == out_dir / "user_study_metrics.json"

    rows = list(
        csv.DictReader(
            outputs["tsv"].read_text(encoding="utf-8").splitlines(),
            delimiter="\t",
        )
    )
    assert [row["row_id"] for row in rows] == [
        "prompt_only_argus",
        "prompt_only_codex",
        "manual_followup_annotation",
    ]
    assert rows[0]["source_kind"] == "prompt_only_condition"
    assert rows[1]["zero_touch_success_rate"] == "0.8333333333333334"
    assert rows[1]["needs_human_rate"] == "0.16666666666666666"
    assert rows[0]["human_interactions_after_assignment_total"] == "0"
    assert rows[1]["manual_commands_total"] == "0"
    assert rows[2]["zero_touch_success_rate"] == "0"
    assert rows[2]["human_interactions_after_assignment_total"] == "2"
    assert rows[2]["active_touch_minutes_after_assignment_total"] == "6.0"
    assert rows[2]["manual_commands_total"] == "1"
    assert rows[2]["manual_rescue_counts"] == '{"failed": 1}'
    assert rows[2]["intervention_severity_counts"] == '{"manual_rescue": 1}'

    for row in rows:
        for key in ("source_bundle", "evidence_1", "evidence_2", "evidence_3"):
            assert (_repo_root() / row[key]).exists()

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["artifact_name"] == "user_study_metrics"
    assert payload["artifact_version"] == 1
    assert payload["source_bundles"]["prompt_only_argus"].endswith(
        "prompt-only-tb2-smoke-20260515T1435Z"
    )
    assert payload["source_bundles"]["manual_followup_annotation"].endswith(
        "tb2-manual-followup-20260515T202500Z"
    )


def test_user_study_generator_cli_produces_checked_in_artifact() -> None:
    repo = _repo_root()
    script = repo / "paper" / "build_user_study_artifacts.py"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )

    artifact = repo / "paper" / "artifacts" / "user_study_metrics.tsv"
    assert artifact.exists()
    content = artifact.read_text(encoding="utf-8")
    assert "prompt_only_argus" in content
    assert "prompt_only_codex" in content
    assert "manual_followup_annotation" in content
    assert "benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z/logs/summary.json" in content
    assert "benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv" in content


def test_user_study_claim_table_paths_resolve() -> None:
    repo = _repo_root()
    claim_table = repo / "paper" / "claims_to_evidence.tsv"
    with claim_table.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    metrics = next(row for row in rows if row["claim_id"] == "user_study_metrics")
    for key in ("evidence_1", "evidence_2", "evidence_3"):
        assert metrics[key]
        assert (repo / metrics[key]).exists()

    user_study = (repo / "paper" / "user-study.md").read_text(encoding="utf-8")
    figures = (repo / "paper" / "figures_tables.md").read_text(encoding="utf-8")
    assert "user_study_metrics.tsv" in user_study
    assert "build_user_study_artifacts.py" in user_study
    assert "user_study_metrics.tsv" in figures
    assert "build_user_study_artifacts.py" in figures
