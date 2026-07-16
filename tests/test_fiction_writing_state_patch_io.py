"""Tests for the state_patch generation-reliability layer (:mod:`state_patch_io`).

The engine (:mod:`state`) is the gate; this layer must (1) surface the exact
referenceable ids for grounding, (2) diagnose a bad patch without raising, and
(3) run a bounded validate→repair loop that can ONLY turn a reject into an
accept via a genuinely-valid revision — never launder a bad patch through.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    new_state,
)
from argus_skill.verticals.fiction_writing.state_patch_io import (
    ALLOWED_OPS,
    apply_patch_with_repair,
    build_generation_context,
    diagnose_patch,
    valid_reference_inventory,
)


def _seed():
    s = new_state("zh")
    s, _ = apply_patch(s, {"patch_id": "p0", "ops": [
        {"op": "add_character", "id": "lin", "value": {"name": "林黛玉"}},
        {"op": "add_location", "id": "xiaoxiang", "value": {"name": "潇湘馆"}},
    ]})
    return s


def test_allowed_ops_match_engine_handlers():
    from argus_skill.verticals.fiction_writing.state import _HANDLERS
    assert set(ALLOWED_OPS) == set(_HANDLERS)


def test_inventory_lists_referenceable_ids():
    inv = valid_reference_inventory(_seed())
    assert inv["characters"] == {"lin": "林黛玉"}
    assert inv["locations"] == {"xiaoxiang": "潇湘馆"}
    assert inv["items"] == {}
    # a brand-new state references nothing
    assert valid_reference_inventory(new_state("zh"))["characters"] == {}


def test_generation_context_grounds_on_real_ids_and_ops():
    ctx = build_generation_context(_seed(), language="zh")
    assert "add_item" in ctx and "add_relationship" in ctx      # op contract present
    assert "lin" in ctx and "xiaoxiang" in ctx                   # existing ids surfaced
    assert "没有删除 op" in ctx                                    # hard rule present
    en = build_generation_context(_seed(), language="en")
    assert "there is no delete op" in en


def test_diagnose_ok_and_structured_failure():
    seed = _seed()
    good = {"patch_id": "g", "ops": [
        {"op": "add_item", "value": {"id": "yu", "name": "通灵宝玉", "holder": "lin"}}]}
    assert diagnose_patch(seed, good)["ok"] is True

    bad = {"patch_id": "b", "ops": [
        {"op": "add_item", "value": {"id": "yu", "name": "通灵宝玉", "holder": "ghost"}}]}
    d = diagnose_patch(seed, bad)
    assert d["ok"] is False
    assert "unknown holder" in d["error"] and "ghost" in d["error"]
    assert d["valid"]["characters"] == {"lin": "林黛玉"}         # inventory for grounding


def test_repair_loop_fixes_bad_holder_using_inventory():
    seed = _seed()
    bad = {"patch_id": "b1", "ops": [
        {"op": "add_item", "value": {"id": "yu", "name": "通灵宝玉", "holder": "ghost"}}]}

    def repair(patch, diag):
        # grounded fix: point the holder at a REAL character id from the diagnosis
        real = next(iter(diag["valid"]["characters"]))
        ops = [{**op, "value": {**op["value"], "holder": real}}
               if op.get("op") == "add_item" else op for op in patch["ops"]]
        return {**patch, "ops": ops}

    state, result, attempts = apply_patch_with_repair(seed, bad, repair, max_attempts=2)
    assert result["applied"] is True
    assert state["items"]["yu"]["holder"] == "lin"
    assert len(attempts) == 1
    assert "unknown holder" in attempts[0]["error"]


def test_repair_loop_converges_on_second_attempt():
    seed = _seed()
    bad = {"patch_id": "b2", "ops": [
        {"op": "add_item", "value": {"id": "jin", "name": "金锁", "holder": "nobody"}}]}
    calls = {"n": 0}

    def stubborn_repair(patch, diag):
        # only produces a valid fix on the SECOND repair call
        calls["n"] += 1
        holder = "lin" if calls["n"] >= 2 else "still-wrong"
        ops = [{**op, "value": {**op["value"], "holder": holder}} for op in patch["ops"]]
        return {**patch, "ops": ops}

    state, result, attempts = apply_patch_with_repair(seed, bad, stubborn_repair, max_attempts=2)
    assert result["applied"] is True and state["items"]["jin"]["holder"] == "lin"
    assert len(attempts) == 2


def test_unfixable_patch_still_raises_engine_is_the_gate():
    # repair that never fixes anything -> the engine must still REJECT (no laundering)
    seed = _seed()
    bad = {"patch_id": "b3", "ops": [
        {"op": "add_item", "value": {"id": "x", "name": "扇", "holder": "ghost"}}]}
    with pytest.raises(PatchError):
        apply_patch_with_repair(seed, bad, lambda p, d: p, max_attempts=2)
    # and the seed state was never mutated
    assert "x" not in seed["items"]


def test_max_attempts_zero_raises_immediately_without_repair():
    seed = _seed()
    bad = {"patch_id": "b4", "ops": [
        {"op": "add_item", "value": {"id": "x", "name": "扇", "holder": "ghost"}}]}
    calls = {"n": 0}

    def counting(patch, diag):
        calls["n"] += 1
        return patch

    with pytest.raises(PatchError):
        apply_patch_with_repair(seed, bad, counting, max_attempts=0)
    assert calls["n"] == 0  # repair never invoked when budget is 0
