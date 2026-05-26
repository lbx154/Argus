from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from paper.build_verifier_gate_artifacts import build_verifier_gate_artifacts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_verifier_gate_generator_writes_expected_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "paper" / "artifacts"
    outputs = build_verifier_gate_artifacts(out_dir)

    assert outputs["tsv"] == out_dir / "verifier_gate_contrast.tsv"
    assert outputs["csv"] == out_dir / "verifier_gate_contrast.csv"
    assert outputs["json"] == out_dir / "verifier_gate_contrast.json"

    rows = list(
        csv.DictReader(
            outputs["tsv"].read_text(encoding="utf-8").splitlines(),
            delimiter="\t",
        )
    )
    assert [row["row_id"] for row in rows] == [
        "reviewer_off_failure",
        "verifier_gated_repair",
        "manual_followup_annotation",
    ]
    assert rows[0]["status"] == "historical_only"
    assert rows[1]["status"] == "current_evidence"
    assert rows[2]["status"] == "current_evidence"
    assert rows[0]["evidence_1"].endswith(
        "tb2-reviewer-gate-contrast-20260515T201700Z/summary.tsv"
    )
    assert rows[1]["evidence_2"].endswith(
        "tb2-reviewer-gate-contrast-20260515T201700Z/jobs/raw/fix-verifier-gated/result.json"
    )
    assert rows[2]["evidence_2"].endswith(
        "tb2-manual-followup-20260515T202500Z/logs/results.csv"
    )

    for row in rows:
        for key in ("source_bundle", "evidence_1", "evidence_2", "evidence_3"):
            assert (_repo_root() / row[key]).exists()

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["artifact_name"] == "verifier_gate_contrast"
    assert payload["artifact_version"] == 1
    assert payload["artifact_label"] == "verifier_gate_contrast"
    assert payload["source_bundles"]["reviewer_off_failure"].endswith(
        "tb2-reviewer-gate-contrast-20260515T201700Z"
    )
    assert payload["source_bundles"]["manual_followup_annotation"].endswith(
        "tb2-manual-followup-20260515T202500Z"
    )


def test_verifier_gate_generator_cli_produces_checked_in_artifact() -> None:
    repo = _repo_root()
    script = repo / "paper" / "build_verifier_gate_artifacts.py"
    subprocess.run(
        [sys.executable, str(script)],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )

    artifact = repo / "paper" / "artifacts" / "verifier_gate_contrast.tsv"
    assert artifact.exists()
    content = artifact.read_text(encoding="utf-8")
    assert "reviewer_off_failure" in content
    assert "verifier_gated_repair" in content
    assert "manual_followup_annotation" in content
    assert "paper/artifacts/verifier_gate_contrast.tsv" not in content


def test_verifier_gate_claim_table_paths_resolve() -> None:
    repo = _repo_root()
    claim_table = repo / "paper" / "claims_to_evidence.tsv"
    with claim_table.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    artifact_rows = {
        row["claim_id"]: row
        for row in rows
        if row["claim_id"] in {"reviewer_off_failure", "verifier_gated_repair", "verifier_gate_contrast"}
    }
    assert set(artifact_rows) == {
        "reviewer_off_failure",
        "verifier_gated_repair",
        "verifier_gate_contrast",
    }

    for row in artifact_rows.values():
        assert "paper/artifacts/verifier_gate_contrast.tsv" in {
            row["evidence_1"],
            row["evidence_2"],
            row["evidence_3"],
        }
        for key in ("evidence_1", "evidence_2", "evidence_3"):
            assert row[key]
            assert (repo / row[key]).exists()

    docs = {
        "single": (repo / "paper" / "single-agent-failure.md").read_text(encoding="utf-8"),
        "tables": (repo / "paper" / "figures_tables.md").read_text(encoding="utf-8"),
        "abstract": (repo / "paper" / "abstract.md").read_text(encoding="utf-8"),
        "intro": (repo / "paper" / "introduction.md").read_text(encoding="utf-8"),
        "eval": (repo / "paper" / "evaluation.md").read_text(encoding="utf-8"),
    }
    for text in docs.values():
        assert "paper/artifacts/verifier_gate_contrast.tsv" in text
        assert "verifier_gate_contrast" in text
