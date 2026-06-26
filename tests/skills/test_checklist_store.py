"""Tests for the per-project, stage-keyed checklist store (``skills/checklist_store``)."""
from __future__ import annotations

import json

from argus_skill.skills import checklist_store as cs


def test_absent_stage_returns_none_present_empty_returns_tuple(tmp_path):
    # Nothing authored yet → None for every stage (signals "fall back to seed").
    assert cs.store_items_for_stage(tmp_path, "scope") is None
    # Author one item, then a different stage stays None.
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "scope", "id": "scope.obj", "statement": "state it", "evidence_hint": "x"},
    ])
    items = cs.store_items_for_stage(tmp_path, "scope")
    assert items is not None and [i.id for i in items] == ["scope.obj"]
    assert cs.store_items_for_stage(tmp_path, "simulate") is None


def test_remove_to_empty_is_honored_as_empty_not_none(tmp_path):
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "scope", "id": "scope.obj", "statement": "x", "evidence_hint": "y"},
    ])
    cs.apply_checklist_ops(tmp_path, [{"op": "remove", "stage": "scope", "id": "scope.obj"}])
    # Stage key present but list empty → () (Planner deliberately emptied it), NOT None.
    assert cs.store_items_for_stage(tmp_path, "scope") == ()


def test_seed_copies_active_vertical_reference(tmp_path):
    # With the default research vertical, seeding 'research' copies the floor items.
    res = cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "research", "id": ""}])
    assert res["applied"] == 1
    ids = [i.id for i in cs.store_items_for_stage(tmp_path, "research")]
    assert "research.literature" in ids                # came from the seed reference
    # Seeding again is a no-op (does not clobber edits).
    res2 = cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "research", "id": ""}])
    assert res2["applied"] == 0 and res2["skipped"] == 1


def test_modify_and_revision_bump(tmp_path):
    cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "scope", "id": "scope.obj", "statement": "old", "evidence_hint": "y"},
    ])
    cs.apply_checklist_ops(tmp_path, [
        {"op": "modify", "stage": "scope", "id": "scope.obj", "statement": "new"},
    ])
    item = cs.store_items_for_stage(tmp_path, "scope")[0]
    assert item.statement == "new"
    raw = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert raw["revision"] >= 2


def test_malformed_rows_dropped_on_read(tmp_path):
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"revision": 1, "stages": {"scope": [
        {"id": "good", "statement": "ok", "evidence_hint": ""},
        {"id": "", "statement": "no id"},          # dropped
        {"statement": "no id key"},                # dropped
        "not a dict",                               # dropped
    ]}}))
    items = cs.store_items_for_stage(tmp_path, "scope")
    assert [i.id for i in items] == ["good"]


def test_protected_floor_item_not_removable_on_paper_vertical(tmp_path):
    # The default vertical is research (gate full_emnlp); protected ids are frozen.
    # Seed run stage then try to remove the protected run.score_variance.
    cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "run", "id": ""}])
    before = {i.id for i in cs.store_items_for_stage(tmp_path, "run")}
    assert "run.score_variance" in before
    res = cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "run", "id": "run.score_variance"},
    ])
    after = {i.id for i in cs.store_items_for_stage(tmp_path, "run")}
    assert "run.score_variance" in after               # refused
    assert res["skipped"] >= 1


def test_per_stage_cap(tmp_path):
    ops = [
        {"op": "add", "stage": "scope", "id": f"scope.{i}", "statement": "x", "evidence_hint": "y"}
        for i in range(cs.MAX_ITEMS_PER_STAGE + 5)
    ]
    cs.apply_checklist_ops(tmp_path, ops)
    items = cs.store_items_for_stage(tmp_path, "scope")
    assert len(items) == cs.MAX_ITEMS_PER_STAGE
