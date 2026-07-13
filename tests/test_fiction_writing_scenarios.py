"""Continuity scenarios. Two kinds:

1. Structural breaks the deterministic engine itself refuses (id integrity,
   duplicate add, timeline clash) — asserted directly here.
2. Semantic continuity breaks (dead character returns, impossible knowledge,
   item teleport in prose, language drift) that a reviewer LLM must catch —
   stored as well-formed eval fixtures in evaluations/continuation_cases.json.
   This file only asserts the fixtures are well-formed and their setup builds a
   schema-valid state; the live reviewer eval is a P3 step."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import argus_skill.verticals.fiction_writing as fw
from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    validate_state,
)

_CASES_PATH = Path(fw.__file__).resolve().parent / "evaluations" / "continuation_cases.json"


# --------------------------------------------------------------------------- #
# 1. structural breaks the engine refuses mechanically
# --------------------------------------------------------------------------- #
def test_engine_refuses_foreshadow_payoff_before_plant():
    with pytest.raises(PatchError):
        apply_patch(None, {"patch_id": "p", "ops": [
            {"op": "resolve_foreshadowing", "id": "f_ghost"},
        ]})


def test_engine_tracks_knowledge_additively():
    s, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "add_character", "id": "c_lin", "value": {"name": "林", "knows": ["k1"]}},
    ]})
    s, _ = apply_patch(s, {"patch_id": "p2", "ops": [
        {"op": "update_character", "id": "c_lin", "set": {"knows": ["k1", "k2"]}},
    ]})
    assert s["characters"]["c_lin"]["knows"] == ["k1", "k2"]


def test_engine_refuses_co_timeline_order_clash():
    s, _ = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "a"}},
    ]})
    with pytest.raises(PatchError):
        apply_patch(s, {"patch_id": "p2", "ops": [
            {"op": "add_timeline", "value": {"id": "t2", "order": 1, "label": "clash"}},
        ]})


# --------------------------------------------------------------------------- #
# 2. semantic eval fixtures — well-formed + sound setup
# --------------------------------------------------------------------------- #
def _load_cases():
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def test_eval_fixtures_present_and_cover_key_scenarios():
    data = _load_cases()
    ids = {c["id"] for c in data["cases"]}
    assert {
        "dead_character_returns_zh", "impossible_knowledge_zh",
        "item_teleport_en", "language_drift_en_to_zh",
    } <= ids


@pytest.mark.parametrize("case", _load_cases()["cases"], ids=lambda c: c["id"])
def test_eval_case_setup_builds_valid_state_and_uses_known_types(case):
    vocab = set(_load_cases()["continuity_vocabulary"])
    # expected finding types must be from the shared continuity vocabulary
    assert case["expected_finding_types"], f"{case['id']}: no expected types"
    assert set(case["expected_finding_types"]) <= vocab
    assert case["language"] in ("zh", "en")
    assert case["draft"].strip(), f"{case['id']}: empty draft"
    # folding the setup patches must yield a schema-valid prior state
    state = None
    for patch in case["setup_patches"]:
        state, result = apply_patch(state, patch)
        assert result["applied"] is True
    assert state is not None
    validate_state(state)
