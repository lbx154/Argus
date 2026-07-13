"""state_patch schema contract (v3, per-op oneOf) + failed-demo artifact (D).
Deterministic, no network, no LLM."""
from __future__ import annotations

import copy
import json

import pytest

import argus_skill.verticals.fiction_writing.evaluations.run_evals as rev
from argus_skill.verticals.fiction_writing.state import (
    PatchError,
    apply_patch,
    validate_patch,
)


def _p(patch_id, *ops):
    return {"patch_id": patch_id, "language": "zh", "ops": list(ops)}


# --- the zh-demo failure is now a schema rejection, not a deep-engine one --- #
def test_add_foreshadowing_missing_value_id_rejected_by_schema():
    with pytest.raises(PatchError):
        validate_patch(_p("p", {"op": "add_foreshadowing", "value": {"statement": "a bell tolls"}}))


def test_valid_add_foreshadowing_passes_schema_and_engine():
    p = _p("p", {"op": "add_foreshadowing", "value": {"id": "fs_1", "statement": "a bell tolls"}})
    validate_patch(p)
    state, res = apply_patch(None, p)
    assert res["applied"] and state["foreshadowing"][0]["id"] == "fs_1"


_LEGAL = [
    {"op": "set_meta", "set": {"title": "T"}},
    {"op": "add_character", "id": "c1", "value": {"name": "A"}},
    {"op": "add_character", "value": {"id": "c2", "name": "B"}},
    {"op": "update_character", "id": "c1", "set": {"status": "dead"}},
    {"op": "add_relationship", "value": {"from": "c1", "to": "c2", "type": "sib"}},
    {"op": "add_world_rule", "value": {"id": "wr1", "statement": "g"}},
    {"op": "add_location", "id": "l1", "value": {"name": "L"}},
    {"op": "add_item", "id": "i1", "value": {"name": "K"}},
    {"op": "move_item", "id": "i1", "to_holder": "c1"},
    {"op": "move_item", "id": "i1", "to_location": "l1"},
    {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "x"}},
    {"op": "add_open_thread", "value": {"id": "th1", "statement": "s"}},
    {"op": "resolve_thread", "id": "th1"},
    {"op": "add_foreshadowing", "value": {"id": "f1", "statement": "s"}},
    {"op": "resolve_foreshadowing", "id": "f1"},
    {"op": "add_chapter_summary", "chapter": 1, "summary": "s"},
]
_LEGAL_IDS = ["set_meta", "add_character/op-id", "add_character/value-id", "update_character",
              "add_relationship", "add_world_rule", "add_location", "add_item",
              "move_item/holder", "move_item/location", "add_timeline", "add_open_thread",
              "resolve_thread", "add_foreshadowing", "resolve_foreshadowing", "add_chapter_summary"]


@pytest.mark.parametrize("op", _LEGAL, ids=_LEGAL_IDS)
def test_legal_op_validates(op):
    validate_patch(_p("p", op))


_ILLEGAL = {
    "no id anywhere": {"op": "add_character", "value": {"name": "A"}},
    "empty name": {"op": "add_character", "id": "c1", "value": {"name": ""}},
    "name wrong type": {"op": "add_character", "id": "c1", "value": {"name": 123}},
    "update no id": {"op": "update_character", "set": {"status": "dead"}},
    "update bad set key": {"op": "update_character", "id": "c1", "set": {"bogus": 1}},
    "relationship no type": {"op": "add_relationship", "value": {"from": "c1", "to": "c2"}},
    "timeline order not int": {"op": "add_timeline", "value": {"id": "t1", "order": "1", "label": "x"}},
    "open_thread no value.id": {"op": "add_open_thread", "value": {"statement": "s"}},
    "open_thread op-level id": {"op": "add_open_thread", "id": "th1", "value": {"id": "th1", "statement": "s"}},
    "chapter_summary no chapter": {"op": "add_chapter_summary", "summary": "s"},
    "move both targets": {"op": "move_item", "id": "i1", "to_holder": "c1", "to_location": "l1"},
    "move no target": {"op": "move_item", "id": "i1"},
    "unknown op": {"op": "delete_everything"},
    "bogus top-level key": {"op": "add_character", "id": "c1", "value": {"name": "A"}, "bogus": 1},
    "GAP#2 set_meta carries id": {"op": "set_meta", "id": "x", "set": {"title": "T"}},
    "GAP#3 value unknown key": {"op": "add_foreshadowing", "value": {"id": "f1", "statement": "s", "junk": 1}},
    "GAP#5 dual id differ": {"op": "add_character", "id": "c1", "value": {"id": "c2", "name": "A"}},
    "GAP#5 dual id equal": {"op": "add_character", "id": "c1", "value": {"id": "c1", "name": "A"}},
}


@pytest.mark.parametrize("op", list(_ILLEGAL.values()), ids=list(_ILLEGAL.keys()))
def test_illegal_op_rejected(op):
    with pytest.raises(PatchError):
        validate_patch(_p("p", op))


def test_id_placement_op_or_value_but_not_both():
    validate_patch(_p("p", {"op": "add_character", "id": "c1", "value": {"name": "A"}}))
    validate_patch(_p("p", {"op": "add_character", "value": {"id": "c2", "name": "B"}}))
    with pytest.raises(PatchError):
        validate_patch(_p("p", {"op": "add_character", "id": "c1", "value": {"id": "c1", "name": "A"}}))


# --- unique patch_id per case; validate structure first; then engine rejects; atomic --- #
def test_semantic_errors_are_structurally_valid_then_engine_rejects():
    base, _ = apply_patch(None, _p("setup", {"op": "add_character", "id": "c_a", "value": {"name": "A"}}))
    before = copy.deepcopy(base)
    cases = {
        "dup_id":       _p("dup", {"op": "add_character", "id": "c_a", "value": {"name": "Dup"}}),
        "unknown_upd":  _p("upd", {"op": "update_character", "id": "c_ghost", "set": {"status": "dead"}}),
        "unknown_move": _p("mov", {"op": "move_item", "id": "i_ghost", "to_holder": "c_a"}),
        "unknown_thr":  _p("rth", {"op": "resolve_thread", "id": "th_ghost"}),
    }
    for name, patch in cases.items():
        validate_patch(patch)                       # structurally legal — schema does NOT reject
        with pytest.raises(PatchError):
            apply_patch(base, patch)                # engine rejects on the ref/duplicate
        assert base == before, f"{name}: prior state mutated (atomicity broken)"


# --- D: failed-demo artifact persistence (tmp_path + FW_EVAL_ARTIFACTS) --- #
def test_failed_demo_artifact_written_to_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path / "arts"))
    patch = {"patch_id": "ch1", "ops": [
        {"op": "add_character", "id": "c1", "value": {"name": "A"}},
        {"op": "add_foreshadowing", "value": {"statement": "no id"}},
    ]}
    rev._dump_failed_demo("demo_zh", "正文草稿", patch,
                          "op[1] (add_foreshadowing): add_foreshadowing: missing id")
    out = tmp_path / "arts" / "demo_zh_failed_patch.json"
    assert out.exists()
    art = json.loads(out.read_text(encoding="utf-8"))
    assert art["draft"] == "正文草稿"
    assert art["structured_output"] == patch
    assert art["parsed_ops"] == patch["ops"]
    assert "missing id" in art["error_message"]
    assert art["failing_op_index"] == 1


def test_failed_demo_artifact_null_op_index_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path))
    rev._dump_failed_demo("demo_zh", "d", {"ops": []}, "no non-empty structured patch returned")
    art = json.loads((tmp_path / "demo_zh_failed_patch.json").read_text(encoding="utf-8"))
    assert art["failing_op_index"] is None


def test_failed_demo_artifact_not_written_into_repo_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path))
    before = set(rev.REPORTS.glob("*_failed_patch.json"))
    rev._dump_failed_demo("demo_zh", "d", {"ops": [{"op": "x"}]}, "boom")
    assert set(rev.REPORTS.glob("*_failed_patch.json")) == before   # nothing new in repo reports
    assert (tmp_path / "demo_zh_failed_patch.json").exists()
