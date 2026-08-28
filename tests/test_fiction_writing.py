"""fiction_writing vertical: registration/contract + the safe story_state patch
engine. Deterministic, no network, no LLM — the shared-core backbone."""
from __future__ import annotations

import pytest

from argus_skill.skills.vertical_select import VERTICAL_PURPOSES, VERTICALS
from argus_skill.verticals._base import load_vertical
from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    new_state,
    validate_state,
)


# --------------------------------------------------------------------------- #
# vertical contract + registration
# --------------------------------------------------------------------------- #
def test_vertical_registered_and_distinct_from_research():
    assert "fiction_writing" in VERTICALS
    purpose = VERTICAL_PURPOSES["fiction_writing"].lower()
    # Must read as creative fiction, and explicitly NOT collide with research.
    assert "fiction" in purpose
    assert "not a" in purpose and "literature review" in purpose


def test_vertical_loads_with_expected_contract():
    mod = load_vertical("fiction_writing")
    assert list(mod.STAGE_ORDER) == [
        "intake", "plan", "draft", "state_update", "review", "revise",
    ]
    # open-ended, non-benchmark: reviewer verdict ends it (like learning)
    assert mod.completion_gate == "none"
    assert "FICTION" in mod.role_banner("engineer")


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_new_state_is_schema_valid():
    validate_state(new_state("zh"))
    validate_state(new_state("en", title="The Clock"))


def test_invalid_state_rejected():
    bad = new_state("zh")
    bad["meta"]["language"] = "fr"  # not in enum
    with pytest.raises(PatchError):
        validate_state(bad)


def _add_char(cid, name, **kw):
    return {"op": "add_character", "id": cid, "value": {"name": name, **kw}}


# --------------------------------------------------------------------------- #
# apply engine — happy path + revision
# --------------------------------------------------------------------------- #
def test_apply_basic_patch_bumps_revision_and_records_id():
    patch = {
        "patch_id": "p1", "chapter": 1, "language": "zh",
        "ops": [
            _add_char("c_lin", "林", motivation="守时"),
            {"op": "add_location", "id": "loc_tower", "value": {"name": "钟楼"}},
            {"op": "add_item", "id": "i_wrench",
             "value": {"name": "扳手", "holder": "c_lin"}},
            {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "母亲下葬"}},
            {"op": "add_open_thread", "value": {"id": "th1", "statement": "钟为何慢四分钟"}},
            {"op": "add_chapter_summary", "chapter": 1, "summary": "父亲的秘密初现"},
        ],
    }
    state, result = apply_patch(None, patch)   # init-from-None
    assert result["applied"] is True
    assert state["meta"]["revision"] == 1
    assert state["applied_patches"] == ["p1"]
    assert state["characters"]["c_lin"]["status"] == "alive"
    assert state["items"]["i_wrench"]["holder"] == "c_lin"
    validate_state(state)


def test_apply_is_idempotent_by_patch_id():
    patch = {"patch_id": "p1", "ops": [_add_char("c_a", "A")]}
    s1, r1 = apply_patch(None, patch)
    s2, r2 = apply_patch(s1, patch)  # replay
    assert r1["applied"] is True and r2["applied"] is False
    assert r2["reason"] == "duplicate"
    assert s2["meta"]["revision"] == 1          # not bumped again
    assert list(s2["characters"]) == ["c_a"]     # no duplicate event


# --------------------------------------------------------------------------- #
# safety: no overwrite, referential integrity, no silent deletion, atomicity
# --------------------------------------------------------------------------- #
def test_add_existing_id_is_rejected_not_overwritten():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [_add_char("c_a", "Alice")]})
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [_add_char("c_a", "Impostor")]})
    assert s1["characters"]["c_a"]["name"] == "Alice"  # untouched


def test_update_unknown_character_rejected():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [_add_char("c_a", "A")]})
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            {"op": "update_character", "id": "c_ghost", "set": {"status": "dead"}},
        ]})


def test_move_item_to_unknown_holder_rejected():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        _add_char("c_a", "A"),
        {"op": "add_item", "id": "i_key", "value": {"name": "钥匙", "holder": "c_a"}},
    ]})
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            {"op": "move_item", "id": "i_key", "to_holder": "c_ghost"},
        ]})


def test_relationship_requires_existing_characters():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [_add_char("c_a", "A")]})
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            {"op": "add_relationship",
             "value": {"from": "c_a", "to": "c_b", "type": "sibling"}},
        ]})


def test_update_preserves_undeclared_state():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        _add_char("c_a", "A", motivation="M", notes="N"),
        _add_char("c_b", "B"),
    ]})
    s2, _ = apply_patch(s1, {"patch_id": "p2", "ops": [
        {"op": "update_character", "id": "c_a", "set": {"status": "dead"}},
    ]})
    # c_a's other fields survive; c_b entirely intact.
    assert s2["characters"]["c_a"]["status"] == "dead"
    assert s2["characters"]["c_a"]["motivation"] == "M"
    assert s2["characters"]["c_a"]["notes"] == "N"
    assert s2["characters"]["c_b"]["name"] == "B"


def test_failed_op_is_atomic_no_partial_apply():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [_add_char("c_a", "A")]})
    # 2nd op fails (unknown update) → whole patch rejected, nothing added.
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            _add_char("c_b", "B"),
            {"op": "update_character", "id": "c_ghost", "set": {"status": "dead"}},
        ]})
    assert "c_b" not in s1["characters"]        # input never mutated
    assert s1["meta"]["revision"] == 1


# --------------------------------------------------------------------------- #
# timeline parseability
# --------------------------------------------------------------------------- #
def test_timeline_stays_ordered_and_rejects_duplicate_order():
    s1, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "add_timeline", "value": {"id": "t2", "order": 2, "label": "later"}},
        {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "earlier"}},
    ]})
    assert [t["order"] for t in s1["timeline"]] == [1, 2]  # sorted
    with pytest.raises(PatchError):
        apply_patch(s1, {"patch_id": "p2", "ops": [
            {"op": "add_timeline", "value": {"id": "t3", "order": 1, "label": "clash"}},
        ]})


def test_resolve_unknown_thread_rejected():
    with pytest.raises(PatchError):
        apply_patch(None, {"patch_id": "p1", "ops": [
            {"op": "resolve_thread", "id": "th_ghost"},
        ]})
