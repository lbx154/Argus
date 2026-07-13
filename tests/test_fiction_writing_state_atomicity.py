"""Adversarial ATOMICITY tests for the story_state patch engine (state.apply_patch).

The engine's whole reason to exist is that a chapter's changes are applied
all-or-nothing: a multi-op patch that fails PART WAY THROUGH must leave the prior
state byte-for-byte intact — no half-applied op, no bumped revision, no recorded
patch_id — and re-applying the same patch_id must be a true no-op, not a re-run.

These tests deliberately target the MID-APPLY and bookkeeping guarantees, which
the existing suite does not: the existing "prior state unchanged" tests fail at
the parse/canonicalize stage (before any op runs); here op[0] genuinely mutates
the scratch copy and op[1] fails, exercising the rollback path itself. We do NOT
re-test the semantic reviewer cases (dead-returns etc.) — those are live-eval
fixtures, not the engine's job.
"""
from __future__ import annotations

import copy

import pytest

from argus_skill.verticals.fiction_writing.state import PatchError, apply_patch


def _seed_one_character():
    """A base state with a single character c_a (revision 1, applied_patches=[p0])."""
    base, res = apply_patch(None, {"patch_id": "p0", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}}]})
    assert res["applied"] and base["meta"]["revision"] == 1
    return base


# --------------------------------------------------------------------------- #
# mid-apply rollback: op[0] applied to scratch, op[1] fails -> whole patch void
# --------------------------------------------------------------------------- #

def test_midapply_failure_rolls_back_first_op_and_leaves_input_untouched():
    base = _seed_one_character()
    before = copy.deepcopy(base)
    # op0 (rename) is valid; op1 references a character that does not exist.
    with pytest.raises(PatchError):
        apply_patch(base, {"patch_id": "p1", "ops": [
            {"op": "update_character", "id": "c_a", "set": {"name": "B"}},
            {"op": "add_relationship",
             "value": {"from": "c_a", "to": "c_ghost", "type": "ally"}},
        ]})
    assert base == before                              # nothing leaked
    assert base["characters"]["c_a"]["name"] == "A"    # op0 was rolled back
    assert base["meta"]["revision"] == 1               # failure did not bump
    assert base["applied_patches"] == ["p0"]           # failed patch not recorded


def test_midapply_timeline_clash_rolls_back_the_valid_op_too():
    base, _ = apply_patch(None, {"patch_id": "p0", "ops": [
        {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "a"}}]})
    before = copy.deepcopy(base)
    with pytest.raises(PatchError):
        apply_patch(base, {"patch_id": "p1", "ops": [
            {"op": "add_timeline", "value": {"id": "t2", "order": 2, "label": "b"}},
            {"op": "add_timeline", "value": {"id": "t3", "order": 2, "label": "x"}},
        ]})
    assert base == before
    assert [t["id"] for t in base["timeline"]] == ["t1"]  # t2 did not survive


def test_engine_is_not_corrupted_by_a_failed_patch():
    # after a mid-apply failure a corrected patch (new id) still applies cleanly.
    base = _seed_one_character()
    with pytest.raises(PatchError):
        apply_patch(base, {"patch_id": "p1", "ops": [
            {"op": "add_relationship",
             "value": {"from": "c_a", "to": "c_ghost", "type": "ally"}}]})
    s, r = apply_patch(base, {"patch_id": "p1b", "ops": [
        {"op": "add_character", "id": "c_ghost", "value": {"name": "G"}},
        {"op": "add_relationship",
         "value": {"from": "c_a", "to": "c_ghost", "type": "ally"}}]})
    assert r["applied"] and s["meta"]["revision"] == 2
    assert len(s["relationships"]) == 1


# --------------------------------------------------------------------------- #
# idempotency: duplicate patch_id is a TRUE no-op, not a re-run
# --------------------------------------------------------------------------- #

def test_duplicate_patch_id_is_a_true_noop():
    p = {"patch_id": "p1", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}}]}
    s1, r1 = apply_patch(None, p)
    assert r1["applied"] and s1["meta"]["revision"] == 1
    # Re-applying the SAME patch_id must NOT re-run the ops (which would raise
    # "already exists"); it is a no-op that neither bumps revision nor dups the id.
    s2, r2 = apply_patch(s1, p)
    assert r2["applied"] is False and r2["reason"] == "duplicate"
    assert s2["meta"]["revision"] == 1
    assert s2["applied_patches"] == ["p1"]
    assert s2 == s1


def test_idempotency_is_by_patch_id_not_op_content():
    # the same op under a DIFFERENT patch_id is not deduped — it collides.
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}}]})
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            {"op": "add_character", "id": "c_a", "value": {"name": "A again"}}]})


def test_duplicate_noop_returns_an_independent_copy():
    p = {"patch_id": "p1", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}}]}
    s1, _ = apply_patch(None, p)
    s2, r2 = apply_patch(s1, p)
    assert r2["applied"] is False
    assert s2 is not s1
    s2["characters"]["c_a"]["name"] = "MUT"
    assert s1["characters"]["c_a"]["name"] == "A"  # input state untouched


# --------------------------------------------------------------------------- #
# input immutability + all-or-nothing forward
# --------------------------------------------------------------------------- #

def test_successful_apply_does_not_mutate_input_and_returns_fresh_state():
    base = _seed_one_character()
    before = copy.deepcopy(base)
    s1, _ = apply_patch(base, {"patch_id": "p1", "ops": [
        {"op": "update_character", "id": "c_a", "set": {"name": "B"}}]})
    assert base == before          # a successful apply leaves the input untouched
    assert s1 is not base
    s1["characters"]["c_a"]["name"] = "MUT"
    assert base["characters"]["c_a"]["name"] == "A"


def test_multi_op_success_bumps_revision_once_and_applies_all():
    s, r = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}},
        {"op": "add_character", "id": "c_b", "value": {"name": "B"}},
        {"op": "add_relationship",
         "value": {"from": "c_a", "to": "c_b", "type": "ally"}},
    ]})
    assert r["applied"]
    assert s["meta"]["revision"] == 1           # one bump for the patch, not per-op
    assert set(s["characters"]) == {"c_a", "c_b"}
    assert len(s["relationships"]) == 1
    assert s["applied_patches"] == ["p1"]
