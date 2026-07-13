"""Deterministic id canonicalization: the provider tends to write an add_* id at BOTH
the op top level AND value.id; we reconcile that BEFORE strict validate_patch — dropping
an equal duplicate, erroring on a conflicting one — WITHOUT weakening the v3 schema.
No network, no LLM."""
from __future__ import annotations

import copy

import pytest

from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    canonicalize_patch,
)


def _p(*ops):
    return {"patch_id": "p1", "language": "zh", "ops": list(ops)}


def test_single_op_level_id_valid():
    s, r = apply_patch(None, _p({"op": "add_character", "id": "c1", "value": {"name": "A"}}))
    assert r["applied"] and "c1" in s["characters"]


def test_single_value_level_id_valid():
    s, r = apply_patch(None, _p({"op": "add_character", "value": {"id": "c2", "name": "B"}}))
    assert r["applied"] and "c2" in s["characters"]


def test_equal_dual_id_canonicalized_and_passes():
    patch = _p({"op": "add_character", "id": "c1", "value": {"id": "c1", "name": "Ana"}})
    canon = canonicalize_patch(patch)
    assert "id" not in canon["ops"][0]["value"]      # value.id dropped
    assert canon["ops"][0]["id"] == "c1"             # op-level id kept as canonical
    s, r = apply_patch(None, patch)                  # full path: canonicalize -> validate -> apply
    assert r["applied"] and s["characters"]["c1"]["name"] == "Ana"


def test_conflicting_dual_id_rejected():
    patch = _p({"op": "add_character", "id": "c1", "value": {"id": "c2", "name": "Ana"}})
    with pytest.raises(PatchError):
        canonicalize_patch(patch)
    with pytest.raises(PatchError):
        apply_patch(None, patch)


def test_conflicting_dual_id_leaves_prior_state_unchanged():
    base, _ = apply_patch(None, _p({"op": "add_character", "id": "c_a", "value": {"name": "A"}}))
    before = copy.deepcopy(base)
    with pytest.raises(PatchError):
        apply_patch(base, {"patch_id": "p2", "ops": [
            {"op": "add_character", "id": "c1", "value": {"id": "c2", "name": "X"}}]})
    assert base == before


def test_canonicalize_leaves_single_id_and_other_ops_untouched():
    patch = _p(
        {"op": "add_character", "id": "c1", "value": {"name": "A"}},
        {"op": "add_open_thread", "value": {"id": "th1", "statement": "s"}},
    )
    assert canonicalize_patch(patch) == patch        # nothing to reconcile


def test_equal_dual_id_also_canonicalized_for_location_and_item():
    s, r = apply_patch(None, _p(
        {"op": "add_location", "id": "l1", "value": {"id": "l1", "name": "Tower"}},
        {"op": "add_item", "id": "i1", "value": {"id": "i1", "name": "Key"}},
    ))
    assert r["applied"] and "l1" in s["locations"] and "i1" in s["items"]


def test_does_not_fabricate_missing_id():
    # neither op-level nor value id -> canonicalize leaves it; strict schema still rejects
    patch = _p({"op": "add_character", "value": {"name": "NoId"}})
    assert canonicalize_patch(patch) == patch
    with pytest.raises(PatchError):
        apply_patch(None, patch)
