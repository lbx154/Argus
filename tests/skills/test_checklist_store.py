"""Tests for the per-project, stage-keyed checklist store (``skills/checklist_store``)."""
from __future__ import annotations

import json

import pytest

from argus_skill.skills import checklist_store as cs
from argus_skill.skills.vertical_select import persist_vertical


@pytest.fixture(autouse=True)
def _research_vertical(tmp_path) -> None:
    # Formal routing no longer accepts a user-forced vertical. Seed the same
    # Manager-owned state these low-level checklist tests operate beneath.
    persist_vertical(tmp_path, "research")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"revision": 1, "vertical": "research", "stages": {"scope": [
        {"id": "good", "statement": "ok", "evidence_hint": ""},
        {"id": "", "statement": "no id"},          # dropped
        {"statement": "no id key"},                # dropped
        "not a dict",                               # dropped
    ]}}))
    items = cs.store_items_for_stage(tmp_path, "scope")
    assert [i.id for i in items] == ["good"]


def test_protected_floor_item_not_removable_on_paper_vertical(tmp_path):
    # The research vertical (gate full_paper) freezes protected ids. The Manager
    # decides + persists the vertical before any read (resolve_vertical is
    # fail-hard), so seed research here.
    persist_vertical(tmp_path, "research")
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
    persist_vertical(tmp_path, "research")
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
    persist_vertical(tmp_path, "research")
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"revision": 9, "vertical": "research", "stages": {"run": []}}))
    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "run")}
    assert "run.score_variance" in ids  # re-injected despite the emptied store


def test_protected_floor_reinjected_canonical_when_weakened_directly(tmp_path):
    # A direct edit that REPLACES a protected item's statement with weak text is
    # canonicalized back to the seed statement on read.
    persist_vertical(tmp_path, "research")
    canon = {i.id: i.statement for i in cs.seed_items_for(tmp_path, "run")}["run.score_variance"]
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"revision": 9, "vertical": "research", "stages": {"run": [
        {"id": "run.score_variance", "statement": "N/A trivially satisfied", "evidence_hint": ""},
    ]}}))
    after = {i.id: i.statement for i in cs.store_items_for_stage(tmp_path, "run")}
    assert after["run.score_variance"] == canon  # canonical floor text restored


def test_clinical_benchmark_override_keeps_domain_neutral_antifraud_floor(tmp_path):
    persist_vertical(tmp_path, "research")
    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "add",
            "stage": "benchmark",
            "id": "benchmark.public_source",
            "statement": "Verify the real public clinical source and license.",
            "evidence_hint": "experiments/BENCHMARK_PROVENANCE.json",
        },
        {
            "op": "add",
            "stage": "benchmark",
            "id": "benchmark.claim_boundary",
            "statement": "Preserve uncertainty and separate planned cohorts.",
            "evidence_hint": "research/CLINICAL_EVIDENCE_GATE.md",
        },
    ])

    items = cs.store_items_for_stage(tmp_path, "benchmark")
    by_id = {item.id: item for item in items}
    # Seed-plus-override: custom items and all research-vertical benchmark seeds present.
    assert "benchmark.public_source" in by_id
    assert "benchmark.claim_boundary" in by_id
    # The research-vertical protected floor item is always present.
    assert "benchmark.evaluator_authentic" in by_id
    # Seed items not overridden are also present.
    assert "benchmark.environment_preflight" in by_id
    assert "benchmark.tasks" in by_id
    authenticity = by_id["benchmark.evaluator_authentic"].statement
    assert "clinical or mechanism projects" in authenticity
    assert "Never invent an evaluator" in authenticity
    assert "GenEval" not in authenticity


# ── New behavior: seed-plus-override semantics ───────────────────────────────

def test_removing_math_custom_leaves_six_seeds_active(tmp_path):
    """Removing a custom item from a Math stage must NOT clear the vertical seeds."""
    persist_vertical(tmp_path, "math")

    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "add",
            "stage": "review",
            "id": "review.paper-infrastructure-artifact",
            "statement": "No infra leakage in paper.",
            "evidence_hint": "paper/PAPER_INFRASTRUCTURE_REVIEW.json",
        }
    ])
    cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.paper-infrastructure-artifact"}
    ])

    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert {
        "review.statement-fidelity",
        "review.no-goal-drift",
        "review.lean-not-sufficient",
        "review.open-problem-honesty",
        "review.correctness-novelty-separated",
        "review.novelty-gate",
    }.issubset(ids), f"Got {ids!r}"


def test_math_custom_coexists_with_all_six_seeds(tmp_path):
    """A Math custom review item coexists with all six built-in review seeds."""
    persist_vertical(tmp_path, "math")

    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "add",
            "stage": "review",
            "id": "review.current-certificate-replay",
            "statement": "No certificate replay.",
            "evidence_hint": "review/CERTIFICATE.json",
        }
    ])

    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.current-certificate-replay" in ids
    for seed_id in (
        "review.statement-fidelity",
        "review.no-goal-drift",
        "review.lean-not-sufficient",
        "review.open-problem-honesty",
        "review.correctness-novelty-separated",
        "review.novelty-gate",
    ):
        assert seed_id in ids, f"seed {seed_id!r} missing"


def test_remove_seed_id_records_tombstone_not_row_removal(tmp_path):
    """`remove` on a seed ID tombstones it (hides it) instead of removing a stored row."""
    persist_vertical(tmp_path, "math")

    res = cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.no-goal-drift"}
    ])
    assert res["applied"] >= 1

    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.no-goal-drift" not in ids        # hidden by tombstone
    assert "review.statement-fidelity" in ids        # other seeds active
    assert "review.novelty-gate" in ids

    raw = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert "review.no-goal-drift" in raw.get("disabled", {}).get("review", [])


def test_add_removes_tombstone(tmp_path):
    """`add` for a tombstoned ID un-tombstones it and adds/overrides the item."""
    persist_vertical(tmp_path, "math")

    cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.no-goal-drift"}
    ])
    assert "review.no-goal-drift" not in {i.id for i in cs.store_items_for_stage(tmp_path, "review")}

    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "add",
            "stage": "review",
            "id": "review.no-goal-drift",
            "statement": "Restored: no goal drift.",
            "evidence_hint": "review/audit.md",
        }
    ])
    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.no-goal-drift" in ids

    raw = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert "review.no-goal-drift" not in raw.get("disabled", {}).get("review", [])


def test_modify_removes_tombstone(tmp_path):
    """`modify` for a tombstoned ID un-tombstones it and creates an override."""
    persist_vertical(tmp_path, "math")

    cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.lean-not-sufficient"}
    ])
    assert "review.lean-not-sufficient" not in {i.id for i in cs.store_items_for_stage(tmp_path, "review")}

    cs.apply_checklist_ops(tmp_path, [
        {
            "op": "modify",
            "stage": "review",
            "id": "review.lean-not-sufficient",
            "statement": "Lean compilation is necessary but not sufficient.",
        }
    ])
    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.lean-not-sufficient" in ids

    raw = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert "review.lean-not-sufficient" not in raw.get("disabled", {}).get("review", [])


def test_seed_op_does_not_duplicate_seeds(tmp_path):
    """After a seed op, each seed appears exactly once in the effective list."""
    persist_vertical(tmp_path, "math")

    cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "review", "id": ""}])
    ids = [i.id for i in cs.store_items_for_stage(tmp_path, "review")]
    for seed_id in (
        "review.statement-fidelity",
        "review.no-goal-drift",
        "review.lean-not-sufficient",
        "review.open-problem-honesty",
        "review.correctness-novelty-separated",
        "review.novelty-gate",
    ):
        assert ids.count(seed_id) == 1, f"{seed_id!r} appears {ids.count(seed_id)} times"


def test_backward_compatible_store_without_disabled_field(tmp_path):
    """Legacy stores without a 'disabled' field treat no seeds as tombstoned."""
    persist_vertical(tmp_path, "math")
    path = tmp_path / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "revision": 5,
        "vertical": "math",
        "stages": {
            "review": [
                {"id": "review.current-certificate-replay", "statement": "no replay", "evidence_hint": "x"}
            ]
        }
        # No "disabled" key — legacy format
    }), encoding="utf-8")

    ids = {i.id for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.current-certificate-replay" in ids
    for seed_id in (
        "review.statement-fidelity",
        "review.no-goal-drift",
        "review.lean-not-sufficient",
        "review.open-problem-honesty",
        "review.correctness-novelty-separated",
        "review.novelty-gate",
    ):
        assert seed_id in ids, f"seed {seed_id!r} missing in backward-compat store"


# ── Task 4 regressions: authored-row cap enforcement on modify ─────────────

def test_modify_absent_custom_id_is_skipped(tmp_path):
    """`modify` of an absent custom ID (not a seed ID) must not create a row."""
    persist_vertical(tmp_path, "math")
    cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "review", "id": ""}])
    count_before = len(cs.store_items_for_stage(tmp_path, "review"))
    res = cs.apply_checklist_ops(tmp_path, [{
        "op": "modify", "stage": "review",
        "id": "review.totally-new-custom-id",
        "statement": "Should not be created.",
    }])
    items_after = cs.store_items_for_stage(tmp_path, "review")
    assert res["skipped"] >= 1
    assert len(items_after) == count_before  # no new row appended
    assert not any(i.id == "review.totally-new-custom-id" for i in items_after)


def test_modify_absent_seed_id_creates_override_below_cap(tmp_path):
    """`modify` of an absent seed ID still creates an override when below the cap."""
    persist_vertical(tmp_path, "math")
    cs.apply_checklist_ops(tmp_path, [{"op": "seed", "stage": "review", "id": ""}])
    # "review.no-goal-drift" is a seed ID but has no explicit stored row yet.
    res = cs.apply_checklist_ops(tmp_path, [{
        "op": "modify", "stage": "review",
        "id": "review.no-goal-drift",
        "statement": "Revised: no goal drift allowed.",
    }])
    assert res["applied"] >= 1
    by_id = {i.id: i.statement for i in cs.store_items_for_stage(tmp_path, "review")}
    assert "review.no-goal-drift" in by_id
    assert by_id["review.no-goal-drift"] == "Revised: no goal drift allowed."


def test_modify_absent_seed_at_cap_is_skipped_and_preserves_tombstone(tmp_path):
    """`modify` creating an absent-seed override is refused at cap; tombstone must not be cleared."""
    persist_vertical(tmp_path, "math")
    # Tombstone a seed ID.
    cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.no-goal-drift"}
    ])
    # Fill the stored bucket to the cap with custom items.
    fill_ops = [
        {"op": "add", "stage": "review", "id": f"review.fill-{i}",
         "statement": f"s {i}", "evidence_hint": ""}
        for i in range(cs.MAX_ITEMS_PER_STAGE)
    ]
    cs.apply_checklist_ops(tmp_path, fill_ops)
    raw_mid = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert len(raw_mid["stages"]["review"]) == cs.MAX_ITEMS_PER_STAGE

    # Attempt to create an absent-seed override — must be refused at cap.
    res = cs.apply_checklist_ops(tmp_path, [{
        "op": "modify", "stage": "review",
        "id": "review.no-goal-drift",
        "statement": "Override that must not be created.",
    }])
    assert res["skipped"] >= 1

    raw_after = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert "review.no-goal-drift" in raw_after.get("disabled", {}).get("review", [])


def test_add_at_cap_is_skipped_and_preserves_tombstone(tmp_path):
    """`add` refused at the authored-row cap must not clear a tombstone for the same ID."""
    persist_vertical(tmp_path, "math")
    # Tombstone a seed ID.
    cs.apply_checklist_ops(tmp_path, [
        {"op": "remove", "stage": "review", "id": "review.no-goal-drift"}
    ])
    # Fill to cap.
    fill_ops = [
        {"op": "add", "stage": "review", "id": f"review.fill-{i}",
         "statement": f"s {i}", "evidence_hint": ""}
        for i in range(cs.MAX_ITEMS_PER_STAGE)
    ]
    cs.apply_checklist_ops(tmp_path, fill_ops)

    # Attempt to add the tombstoned seed ID at cap — skipped, tombstone preserved.
    res = cs.apply_checklist_ops(tmp_path, [{
        "op": "add", "stage": "review",
        "id": "review.no-goal-drift",
        "statement": "Restored item that must not be added at cap.",
    }])
    assert res["skipped"] >= 1

    raw_after = json.loads((tmp_path / "research" / "CHECKLISTS.json").read_text())
    assert "review.no-goal-drift" in raw_after.get("disabled", {}).get("review", [])
def test_project_checklist_is_ignored_after_vertical_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    persist_vertical(tmp_path, "research")
    cs.apply_checklist_ops(tmp_path, [{
        "op": "add",
        "stage": "research",
        "id": "research.only",
        "statement": "paper-only gate",
        "evidence_hint": "research/PAPER.md",
    }])
    assert cs.store_items_for_stage(tmp_path, "research") is not None

    persist_vertical(tmp_path, "math")
    assert cs.store_items_for_stage(tmp_path, "research") is None
