"""Tests for project-local DATA domains (``verticals/_data_domain``)."""
from __future__ import annotations

import json

import pytest

from argus_skill.verticals import _data_domain as dd


def test_write_then_load_roundtrip(tmp_path):
    dd.write_data_domain(tmp_path, "robotics_sim", stages=["scope", "simulate", "measure", "report"])
    domain = dd.load_data_domain("robotics_sim", tmp_path)
    assert domain is not None
    assert domain.STAGE_ORDER == ["scope", "simulate", "measure", "report"]
    assert domain.CHECKLIST_STAGE_ORDER == ("scope", "simulate", "measure", "report")
    assert domain.completion_gate == "none"           # fresh domain never demands the paper gate
    assert domain.role_banner("reviewer") == ""
    assert set(domain.STAGE_CHECKS) == set(domain.STAGE_ORDER)
    assert domain.REVIEWER_CHECKLISTS == {}
    assert domain.CHECKLIST_ITEMS == {}               # Planner authors items at runtime


def test_exists_and_list(tmp_path):
    assert dd.list_data_domains(tmp_path) == []
    dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    dd.write_data_domain(tmp_path, "beta", stages=["a", "b"])
    assert dd.data_domain_exists("alpha", tmp_path)
    assert not dd.data_domain_exists("gamma", tmp_path)
    assert dd.list_data_domains(tmp_path) == ["alpha", "beta"]


def test_index_is_written(tmp_path):
    dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    index = json.loads((tmp_path / "research" / "DOMAINS" / "INDEX.json").read_text())
    assert "alpha" in index and index["alpha"]["stages"] == ["a", "b"]


def test_missing_and_corrupt_fail_open_to_none(tmp_path):
    assert dd.load_data_domain("nope", tmp_path) is None
    bad = tmp_path / "research" / "DOMAINS" / "broken.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")
    assert dd.load_data_domain("broken", tmp_path) is None


def test_name_validation_rejects_path_separators(tmp_path):
    assert not dd.is_valid_domain_name("../evil")
    assert not dd.is_valid_domain_name("a/b")
    assert not dd.is_valid_domain_name("Cap")        # uppercase not allowed
    assert dd.is_valid_domain_name("robo_sim2")
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "../evil", stages=["a", "b"])


def test_create_only_unless_overwrite(tmp_path):
    dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    dd.write_data_domain(tmp_path, "alpha", stages=["x", "y"], overwrite=True)
    assert dd.load_data_domain("alpha", tmp_path).STAGE_ORDER == ["x", "y"]


def test_empty_stages_rejected(tmp_path):
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "alpha", stages=[])


def test_seed_checklist_builds_items(tmp_path):
    # A domain JSON may carry a seed checklist; it becomes CHECKLIST_ITEMS.
    path = tmp_path / "research" / "DOMAINS" / "seeded.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "name": "seeded",
        "stages": ["scope", "simulate"],
        "checklist": {"scope": [{"id": "scope.obj", "statement": "state it", "evidence_hint": "x"}]},
    }))
    domain = dd.load_data_domain("seeded", tmp_path)
    assert [i.id for i in domain.CHECKLIST_ITEMS["scope"]] == ["scope.obj"]
