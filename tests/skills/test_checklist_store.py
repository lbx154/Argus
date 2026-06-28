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


def test_protected_floor_not_overwritable_via_add(tmp_path):
    # `add` of a protected id must be refused too (it strips + replaces the floor
    # item) — the round-2 bypass: only modify/remove were guarded.
    cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "run", "id": ""}])
    seed_stmt = {i.id: i.statement for i in cs.store_items_for_stage(tmp_path, "run")}
    res = cs.apply_checklist_ops(tmp_path, [
        {"op": "add", "stage": "run", "id": "run.score_variance",
         "statement": "trivially satisfied", "evidence_hint": ""},
    ])
    after = {i.id: i.statement for i in cs.store_items_for_stage(tmp_path, "run")}
    assert after["run.score_variance"] == seed_stmt["run.score_variance"]  # not weakened
    assert res["skipped"] >= 1


def test_protected_floor_reinjected_when_store_emptied_directly(tmp_path):
    # Bypass apply_checklist_ops entirely: write a CHECKLISTS.json that EMPTIES the
    # run stage (what an unsandboxed engineer subprocess can do). The read path must
    # re-inject the protected floor so the reviewer still sees it.
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"revision": 9, "stages": {"run": []}}))
    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "run")}
    assert "run.score_variance" in ids  # re-injected despite the emptied store


def test_protected_floor_reinjected_canonical_when_weakened_directly(tmp_path):
    # A direct edit that REPLACES a protected item's statement with weak text is
    # canonicalized back to the seed statement on read.
    canon = {i.id: i.statement for i in cs.seed_items_for(tmp_path, "run")}["run.score_variance"]
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"revision": 9, "stages": {"run": [
        {"id": "run.score_variance", "statement": "N/A trivially satisfied", "evidence_hint": ""},
    ]}}))
    after = {i.id: i.statement for i in cs.store_items_for_stage(tmp_path, "run")}
    assert after["run.score_variance"] == canon  # canonical floor text restored
