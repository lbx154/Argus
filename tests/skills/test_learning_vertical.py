from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.learning.curation import validate_curation


def _write_learning_project(root: Path) -> Path:
    page = root / ".autors" / "project" / "wiki" / "pages" / "materials" / "guide.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: guide\ndescription: source\n---\nUse bounded retries for transient failures.",
        encoding="utf-8",
    )
    manifest = root / "learning" / "MATERIAL_MANIFEST.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "materials": [{"source_id": "materials/guide", "char_count": 44}],
    }), encoding="utf-8")
    (root / "learning" / "STUDY.md").write_text("Existing retry guidance is incomplete.", encoding="utf-8")
    target = root / ".autors" / "project" / "skills" / "engineer" / "bounded-retries.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nname: bounded retries\ndescription: retry transient failures\n---\n", encoding="utf-8")
    (root / "learning" / "CHANGE_PLAN.json").write_text(json.dumps({
        "version": 1,
        "operations": [{
            "action": "create",
            "layer": "skill",
            "target": ".autors/project/skills/engineer/bounded-retries.md",
            "reason": "Reusable procedure missing from project skills.",
            "evidence": [{
                "source_id": "materials/guide",
                "locator": "retry guidance",
                "quote": "Use bounded retries for transient failures.",
            }],
        }],
    }), encoding="utf-8")
    index = root / ".autors" / "project" / "wiki" / "INDEX.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Wiki\n\n- [Guide](pages/materials/guide.md)", encoding="utf-8")
    return page


def test_learning_curation_is_evidence_anchored(tmp_path: Path) -> None:
    page = _write_learning_project(tmp_path)
    for stage in ("ingest", "study", "curate", "review"):
        assert validate_curation(tmp_path, stage) == []

    page.write_text("The cited sentence was not in this source.", encoding="utf-8")
    assert "quote is not verbatim" in " ".join(validate_curation(tmp_path, "review"))


def test_learning_noop_is_explicit_and_exclusive(tmp_path: Path) -> None:
    _write_learning_project(tmp_path)
    plan = tmp_path / "learning" / "CHANGE_PLAN.json"
    plan.write_text(json.dumps({
        "version": 1,
        "operations": [{"action": "no_op", "reason": "Already covered faithfully."}],
    }), encoding="utf-8")

    assert validate_curation(tmp_path, "curate") == []


def test_learning_stage_contract_runs_typed_validator(tmp_path: Path) -> None:
    from argus_skill.verticals.learning import stages

    assert "MATERIAL_MANIFEST" in " ".join(stages.stage_completion_issues("ingest", tmp_path))
