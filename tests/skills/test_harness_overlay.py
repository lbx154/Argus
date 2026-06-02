"""Tests for per-project harness self-evolution overlay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills import harness_overlay as ho
from argus_skill.skills.stage_checklists import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    format_full_pipeline_checklist,
    format_stage_checklist,
)


def _known():
    stages = frozenset(CANONICAL_STAGE_ORDER)
    ids = frozenset(it.id for items in STAGE_CHECKLISTS.values() for it in items)
    return stages, ids


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_no_overlay_renders_floor_only(project):
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "run.score_variance" in out
    assert "house rules" not in out.lower()
    assert "Harness floor (non-negotiable)" not in out


def test_resolve_project_root_priority(project, monkeypatch):
    assert ho.resolve_project_root() == project
    assert ho.resolve_project_root("/tmp/explicit") == Path("/tmp/explicit")
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_ROOT")
    assert ho.resolve_project_root() == Path.cwd()


def test_add_engineer_item_activates_and_renders(project):
    stages, ids = _known()
    entry = ho.add_checklist_item(
        project,
        item={
            "id": "run.hparam_log", "stage": "run", "role": "engineer", "op": "add",
            "statement": "Log lr and max_completion_length.",
            "evidence_hint": "experiments/*/cfg.json", "reason": "rl collapse",
        },
        known_stages=stages, known_item_ids=ids,
    )
    assert entry["state"] == "active"
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "run.hparam_log" in out
    assert "Harness floor (non-negotiable)" in out  # floor asserted once something added


def test_role_scoping_engineer_item_not_seen_by_reviewer(project):
    stages, ids = _known()
    ho.add_checklist_item(
        project,
        item={"id": "run.x", "stage": "run", "role": "engineer", "op": "add",
              "statement": "s", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    assert "run.x" not in format_stage_checklist("run", role="reviewer", project_root=project)


def test_reviewer_change_lands_in_pending_not_active(project):
    stages, ids = _known()
    entry = ho.add_checklist_item(
        project,
        item={"id": "run.score_variance", "stage": "run", "role": "reviewer",
              "op": "amend", "note": "confirm learnable-regime knobs", "reason": "noise"},
        known_stages=stages, known_item_ids=ids,
    )
    assert entry["state"] == "pending"
    assert "confirm learnable-regime knobs" not in format_stage_checklist("run", role="reviewer", project_root=project)


def test_promote_pending_then_renders_and_covers_full_pipeline(project):
    stages, ids = _known()
    ho.add_checklist_item(
        project,
        item={"id": "run.score_variance", "stage": "run", "role": "reviewer",
              "op": "amend", "note": "confirm learnable-regime knobs", "reason": "noise"},
        known_stages=stages, known_item_ids=ids,
    )
    assert ho.promote(project, entry_id="run.score_variance")
    assert "confirm learnable-regime knobs" in format_stage_checklist("run", role="reviewer", project_root=project)
    # the submission gate (full pipeline) must also reflect the overlay
    assert "confirm learnable-regime knobs" in format_full_pipeline_checklist(role="reviewer", project_root=project)


def test_protected_item_cannot_be_superseded(project):
    stages, ids = _known()
    with pytest.raises(ho.OverlayValidationError):
        ho.add_checklist_item(
            project,
            item={"id": "run.score_variance", "stage": "run", "role": "engineer",
                  "op": "supersede", "statement": "anything", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )


def test_add_with_colliding_floor_id_rejected(project):
    stages, ids = _known()
    with pytest.raises(ho.OverlayValidationError):
        ho.add_checklist_item(
            project,
            item={"id": "run.score_variance", "stage": "run", "role": "engineer",
                  "op": "add", "statement": "s", "evidence_hint": "e", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )


def test_amend_unknown_floor_item_rejected(project):
    stages, ids = _known()
    with pytest.raises(ho.OverlayValidationError):
        ho.add_checklist_item(
            project,
            item={"id": "run.does_not_exist", "stage": "run", "role": "engineer",
                  "op": "amend", "note": "n", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )


def test_missing_reason_rejected(project):
    stages, ids = _known()
    with pytest.raises(ho.OverlayValidationError):
        ho.add_checklist_item(
            project,
            item={"id": "run.y", "stage": "run", "role": "engineer", "op": "add",
                  "statement": "s", "evidence_hint": "e"},
            known_stages=stages, known_item_ids=ids,
        )


def test_house_rule_renders_for_role(project):
    ho.add_prompt_rule(
        project,
        rule={"id": "eng.r1", "role": "engineer", "text": "Always sanity-check knobs.", "reason": "r"},
    )
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "Project house rules (self-authored, revertible)" in out
    assert "Always sanity-check knobs." in out
    # not for a different role
    assert "Always sanity-check knobs." not in format_stage_checklist("run", role="reviewer", project_root=project)


def test_malformed_overlay_is_failopen(project):
    d = ho.harness_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / "active.json").write_text("{ this is not json", encoding="utf-8")
    # floor still renders, corruption recorded in journal
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "run.score_variance" in out
    journal = (d / "journal.jsonl").read_text(encoding="utf-8")
    assert "overlay_invalid_ignored" in journal


def test_hot_reload_reads_fresh_each_call(project):
    stages, ids = _known()
    assert "run.hot" not in format_stage_checklist("run", role="engineer", project_root=project)
    ho.add_checklist_item(
        project,
        item={"id": "run.hot", "stage": "run", "role": "engineer", "op": "add",
              "statement": "s", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    # same process, no cache invalidation needed — next render reflects the edit
    assert "run.hot" in format_stage_checklist("run", role="engineer", project_root=project)


def test_revert_removes_entry(project):
    stages, ids = _known()
    ho.add_checklist_item(
        project,
        item={"id": "run.tmp", "stage": "run", "role": "engineer", "op": "add",
              "statement": "s", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    assert "run.tmp" in format_stage_checklist("run", role="engineer", project_root=project)
    assert ho.revert(project, entry_id="run.tmp")
    assert "run.tmp" not in format_stage_checklist("run", role="engineer", project_root=project)


def test_reset_stage_scoped(project):
    stages, ids = _known()
    for sid, st in (("run.a", "run"), ("plan.b", "plan")):
        ho.add_checklist_item(
            project,
            item={"id": sid, "stage": st, "role": "engineer", "op": "add",
                  "statement": "s", "evidence_hint": "e", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )
    removed = ho.reset(project, stage="run")
    assert removed == 1
    assert "run.a" not in format_stage_checklist("run", role="engineer", project_root=project)
    assert "plan.b" in format_stage_checklist("plan", role="engineer", project_root=project)


def test_revision_monotonic(project):
    stages, ids = _known()
    ho.add_checklist_item(
        project,
        item={"id": "run.a", "stage": "run", "role": "engineer", "op": "add",
              "statement": "s", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    r1 = ho.load_overlay(project, state="active")["revision"]
    ho.add_checklist_item(
        project,
        item={"id": "run.b", "stage": "run", "role": "engineer", "op": "add",
              "statement": "s", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    r2 = ho.load_overlay(project, state="active")["revision"]
    assert r2 == r1 + 1


def test_item_cap_enforced(project):
    stages, ids = _known()
    for i in range(ho.MAX_ITEMS):
        ho.add_checklist_item(
            project,
            item={"id": f"run.i{i}", "stage": "run", "role": "engineer", "op": "add",
                  "statement": "s", "evidence_hint": "e", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )
    with pytest.raises(ho.OverlayValidationError):
        ho.add_checklist_item(
            project,
            item={"id": "run.overflow", "stage": "run", "role": "engineer", "op": "add",
                  "statement": "s", "evidence_hint": "e", "reason": "r"},
            known_stages=stages, known_item_ids=ids,
        )


def _write_active(project, **overlay):
    d = ho.harness_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    base = {"revision": 1, "checklist_items": [], "prompt_rules": []}
    base.update(overlay)
    (d / "active.json").write_text(json.dumps(base), encoding="utf-8")


def test_read_time_drops_handedited_floor_collision(project):
    # A syntactically valid active.json that smuggles an `add` colliding with a
    # protected floor id must be ignored at render time, not shown.
    _write_active(project, checklist_items=[
        {"id": "run.score_variance", "stage": "run", "role": "engineer", "op": "add",
         "statement": "PWNED relax", "evidence_hint": "x"},
    ])
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "PWNED relax" not in out


def test_read_time_drops_invalid_op_and_role(project):
    _write_active(project, checklist_items=[
        {"id": "run.bad", "stage": "run", "role": "engineer", "op": "nuke", "statement": "x", "evidence_hint": "y"},
        {"id": "run.good", "stage": "run", "role": "engineer", "op": "add", "statement": "kept", "evidence_hint": "y"},
    ])
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "run.bad" not in out
    assert "kept" in out


def test_supersede_renders_additive_not_override(project):
    # supersede is not exposed by the CLI; if hand-authored for a NON-protected
    # floor item it must render as an additional requirement, never an override.
    _write_active(project, checklist_items=[
        {"id": "run.scale", "stage": "run", "role": "engineer", "op": "supersede",
         "statement": "also report wall-clock", "reason": "r"},
    ])
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "additional project requirement" in out
    assert "override" not in out.lower()


def test_supersede_protected_ignored_on_read(project):
    _write_active(project, checklist_items=[
        {"id": "run.score_variance", "stage": "run", "role": "engineer", "op": "supersede",
         "statement": "ignore variance", "reason": "r"},
    ])
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "ignore variance" not in out


def test_snapshot_restore_roundtrip(project):
    stages, ids = _known()
    ho.add_checklist_item(
        project,
        item={"id": "run.keep", "stage": "run", "role": "engineer", "op": "add",
              "statement": "original", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    snap = ho.snapshot(project)
    # re-propose same id (replaces the prior good entry), then roll back
    ho.add_checklist_item(
        project,
        item={"id": "run.keep", "stage": "run", "role": "engineer", "op": "add",
              "statement": "mutated", "evidence_hint": "e", "reason": "r"},
        known_stages=stages, known_item_ids=ids,
    )
    ho.restore(project, snap)
    out = format_stage_checklist("run", role="engineer", project_root=project)
    assert "original" in out
    assert "mutated" not in out

