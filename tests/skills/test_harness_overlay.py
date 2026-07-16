"""Project overlay now owns prompt rules only; Planner owns checklists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills import harness_overlay as ho
from argus_skill.skills.stage_checklists import format_stage_checklist
from argus_skill.skills.vertical_select import persist_vertical


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    persist_vertical(tmp_path, "research")
    return tmp_path


def test_checklist_overlay_mutations_are_rejected(project: Path) -> None:
    with pytest.raises(ho.OverlayValidationError, match="Planner"):
        ho.add_checklist_item(
            project,
            item={
                "id": "run.extra",
                "stage": "run",
                "role": "engineer",
                "op": "add",
                "statement": "extra gate",
                "evidence_hint": "x",
                "reason": "r",
            },
            known_stages=frozenset({"run"}),
            known_item_ids=frozenset(),
        )


def test_legacy_checklist_overlay_rows_are_ignored(project: Path) -> None:
    path = project / ".argus" / "harness" / "active.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "revision": 1,
        "checklist_items": [{
            "id": "run.legacy",
            "stage": "run",
            "role": "engineer",
            "op": "add",
            "statement": "must not render",
        }],
        "prompt_rules": [],
    }))
    assert ho.active_checklist_items(project, stage="run", role="engineer") == []
    assert "run.legacy" not in format_stage_checklist(
        "run", role="engineer", project_root=project
    )


def test_engineer_prompt_rule_activates_and_renders(project: Path) -> None:
    entry = ho.add_prompt_rule(project, rule={
        "id": "eng.preflight",
        "role": "engineer",
        "text": "Check the live configuration before launch.",
        "reason": "recurring mistake",
    })
    assert entry["state"] == "active"
    rendered = format_stage_checklist("run", role="engineer", project_root=project)
    assert "Check the live configuration" in rendered


def test_reviewer_prompt_rule_requires_promotion(project: Path) -> None:
    entry = ho.add_prompt_rule(project, rule={
        "id": "rev.rule",
        "role": "reviewer",
        "text": "Inspect the raw trajectory on contradictory evidence.",
        "reason": "recurring miss",
    })
    assert entry["state"] == "pending"
    before = format_stage_checklist("run", role="reviewer", project_root=project)
    assert "raw trajectory" not in before
    assert ho.promote(project, entry_id="rev.rule") is True
    after = format_stage_checklist("run", role="reviewer", project_root=project)
    assert "raw trajectory" in after


def test_prompt_rule_revert_and_reset(project: Path) -> None:
    for rule_id in ("eng.a", "eng.b"):
        ho.add_prompt_rule(project, rule={
            "id": rule_id,
            "role": "engineer",
            "text": f"rule {rule_id}",
            "reason": "r",
        })
    assert ho.revert(project, entry_id="eng.a") is True
    assert "rule eng.a" not in format_stage_checklist(
        "run", role="engineer", project_root=project
    )
    assert ho.reset(project) >= 1
    assert "rule eng.b" not in format_stage_checklist(
        "run", role="engineer", project_root=project
    )


def test_malformed_overlay_is_fail_open(project: Path) -> None:
    path = project / ".argus" / "harness" / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    rendered = format_stage_checklist("run", role="engineer", project_root=project)
    assert "run.score_variance" in rendered
