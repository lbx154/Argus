from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argus_skill.verticals.research.artifact_freshness import (
    artifact_freshness_issues,
)
from argus_skill.verticals.research.stages import stage_completion_issues


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _review(root: Path, digest: str) -> None:
    path = root / "analysis/final_submission_certification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "artifact_type": "final_submission_certification",
                "source_snapshots": [
                    {"path": "paper/main.tex", "sha256": digest}
                ],
            }
        ),
        encoding="utf-8",
    )


def test_matching_source_snapshot_passes(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("Current manuscript.\n", encoding="utf-8")
    _review(tmp_path, _sha256(manuscript))

    assert artifact_freshness_issues(tmp_path) == ()


def test_stale_source_snapshot_blocks_without_exposing_hashes(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper/main.tex"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("Certified manuscript.\n", encoding="utf-8")
    certified_hash = _sha256(manuscript)
    _review(tmp_path, certified_hash)
    manuscript.write_text("Replacement manuscript.\n", encoding="utf-8")
    current_hash = _sha256(manuscript)

    issues = artifact_freshness_issues(tmp_path)

    assert any("analysis/final_submission_certification.json" in issue for issue in issues)
    assert any("paper/main.tex" in issue for issue in issues)
    assert all(certified_hash[:12] not in issue for issue in issues)
    assert all(current_hash[:12] not in issue for issue in issues)
    assert any("the manuscript has changed since" in issue for issue in issues)
    assert any(
        issue.startswith("[artifact_freshness]")
        for issue in stage_completion_issues("review", tmp_path)
    )


def test_recorded_pdf_scalars_are_rechecked_when_tools_exist(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript = tmp_path / "paper/main.tex"
    pdf = tmp_path / "paper/main.pdf"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("Current manuscript.\n", encoding="utf-8")
    pdf.write_bytes(b"pdf placeholder")
    review = tmp_path / "analysis/final_review.json"
    review.parent.mkdir(parents=True)
    review.write_text(
        json.dumps(
            {
                "source_snapshots": [
                    {"path": "paper/main.tex", "sha256": _sha256(manuscript)}
                ],
                "manuscript_path": "paper/main.pdf",
                "pages": 10,
                "verified_text_fragment": "0.035353",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.artifact_freshness.shutil.which",
        lambda name: name,
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.artifact_freshness._pdf_pages",
        lambda tool, path: 7,
    )
    monkeypatch.setattr(
        "argus_skill.verticals.research.artifact_freshness._pdf_text",
        lambda tool, path: "replacement paper",
    )

    issues = artifact_freshness_issues(tmp_path)

    assert any("recorded 10, actual 7" in issue for issue in issues)
    assert any("'0.035353'" in issue for issue in issues)
