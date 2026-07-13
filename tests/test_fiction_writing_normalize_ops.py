"""Deterministic stringified-ops normalization: a forced tool call sometimes returns
``ops`` as a JSON string instead of an array; we ``json.loads`` it ONCE before strict
validate_patch (the decoded value must be a list), without fabricating ops, without an
LLM repair loop, and without weakening the schema. No network, no LLM."""
from __future__ import annotations

import copy
import json

import pytest

from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    normalize_ops,
)


def test_legal_stringified_ops_restored_and_applied():
    ops = [{"op": "add_character", "id": "c1", "value": {"name": "Ana"}}]
    patch = {"patch_id": "p1", "language": "zh", "ops": json.dumps(ops)}   # ops is a STRING
    norm = normalize_ops(patch)
    assert isinstance(norm["ops"], list)
    s, r = apply_patch(None, patch)   # full path: normalize -> canonicalize -> validate -> apply
    assert r["applied"] and "c1" in s["characters"]


def test_stringified_ops_invalid_json_rejected():
    patch = {"patch_id": "p1", "ops": "[not valid json"}
    with pytest.raises(PatchError):
        normalize_ops(patch)
    with pytest.raises(PatchError):
        apply_patch(None, patch)


def test_stringified_ops_decoding_to_non_array_rejected():
    patch = {"patch_id": "p1", "ops": json.dumps({"op": "add_character"})}  # decodes to dict, not list
    with pytest.raises(PatchError):
        normalize_ops(patch)
    with pytest.raises(PatchError):
        apply_patch(None, patch)


def test_list_ops_unaffected():
    patch = {"patch_id": "p1", "ops": [{"op": "add_character", "id": "c1", "value": {"name": "A"}}]}
    assert normalize_ops(patch) == patch          # already a list -> unchanged
    s, r = apply_patch(None, patch)
    assert r["applied"] and "c1" in s["characters"]


def test_rejection_leaves_prior_state_unchanged():
    base, _ = apply_patch(None, {"patch_id": "p0", "ops": [
        {"op": "add_character", "id": "c_a", "value": {"name": "A"}}]})
    before = copy.deepcopy(base)
    with pytest.raises(PatchError):
        apply_patch(base, {"patch_id": "p2", "ops": "[not valid json"})
    assert base == before


def test_stringified_ops_then_dual_id_canonicalization_compose():
    # stringified ops whose add_character carries an EQUAL dual id: normalize (str->list)
    # then canonicalize (drop value.id) must compose and apply cleanly.
    ops = [{"op": "add_character", "id": "c1", "value": {"id": "c1", "name": "Ana"}}]
    patch = {"patch_id": "p1", "language": "zh", "ops": json.dumps(ops)}
    s, r = apply_patch(None, patch)
    assert r["applied"] and s["characters"]["c1"]["name"] == "Ana"
