from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from paper.build_hierarchy_artifacts import build_hierarchy_artifacts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_hierarchy_generator_writes_expected_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "paper" / "artifacts"
    outputs = build_hierarchy_artifacts(out_dir)

    assert outputs["tsv"] == out_dir / "slm_llm_human_hierarchy.tsv"
    assert outputs["csv"] == out_dir / "slm_llm_human_hierarchy.csv"
    assert outputs["json"] == out_dir / "slm_llm_human_hierarchy.json"

    rows = list(
        csv.DictReader(
            outputs["tsv"].read_text(encoding="utf-8").splitlines(),
            delimiter="\t",
        )
    )
    assert [row["tier"] for row in rows] == ["SLM", "LLM", "HUMAN"]
    for row in rows:
        for key in ("source_bundle", "evidence_1", "evidence_2", "evidence_3"):
            assert (_repo_root() / row[key]).exists()

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["hierarchy_label"] == "SLM->LLM->HUMAN"
    assert payload["artifact_version"] == 1


def test_hierarchy_generator_cli_produces_checked_in_artifact(tmp_path: Path) -> None:
    repo = _repo_root()
    script = repo / "paper" / "build_hierarchy_artifacts.py"
    # Write to tmp_path, NOT paper/artifacts/ — the latter is git-tracked and the
    # script stamps an absolute repo_root into the JSON, so running against the
    # real dir pollutes the working tree with a spurious diff.
    out_dir = tmp_path / "artifacts"
    subprocess.run(
        [sys.executable, str(script), "--output-dir", str(out_dir)],
        cwd=repo, check=True, text=True, capture_output=True,
    )

    artifact = out_dir / "slm_llm_human_hierarchy.tsv"
    assert artifact.exists()
    content = artifact.read_text(encoding="utf-8")
    assert "SLM->LLM->HUMAN" in content
    assert "experiments/tb2-bare-gpt54-mini-20260515T212131Z/manifest.json" in content
    assert "experiments/tb2-bare-gpt54-mini-20260515T212131Z/status.json" in content
    assert "benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv" in content


def test_hierarchy_claim_table_paths_resolve() -> None:
    repo = _repo_root()
    claim_table = repo / "paper" / "claims_to_evidence.tsv"
    with claim_table.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    hierarchy = next(row for row in rows if row["claim_id"] == "slm_llm_human_hierarchy")
    for key in ("evidence_1", "evidence_2", "evidence_3"):
        assert hierarchy[key]
        assert (repo / hierarchy[key]).exists()
