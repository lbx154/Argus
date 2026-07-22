"""Closed-loop tests for the shared literary Source Registry contract.

Covers load + structural/semantic validation, the read-only query, and the
rights gate (assert_use_allowed) — plus every rejection the acceptance definition
requires: duplicate ids, unknown provider, out-of-vocabulary uses, allowed/
prohibited overlap, unknown rights_status, and the ingestion honesty guard
(ingested=true needs a checksum AND cleared rights).
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.literary.shared.source_registry import (
    USES_REQUIRING_INGESTION,
    RegistryError,
    assert_use_allowed,
    load_registry,
    query,
    validate_registry,
)

_VOCAB = ["query_only", "human_research", "evidence_citation",
          "local_indexing", "model_training", "redistribution"]


def _item(iid="s1", provider="p1", allowed=None, prohibited=None,
          rights="pending_review", ingested=False, checksum=None):
    it = {
        "id": iid,
        "provider": provider,
        "rights_status": rights,
        "allowed_uses": ["query_only"] if allowed is None else allowed,
        "ingested": ingested,
    }
    if prohibited is not None:
        it["prohibited_uses"] = prohibited
    if checksum is not None:
        it["checksum"] = checksum
    return it


def _registry(items=None, providers=None, vocab=None):
    return {
        "allowed_use_vocabulary": list(vocab) if vocab is not None else list(_VOCAB),
        "providers": providers if providers is not None else [{"id": "p1", "name": "P1"}],
        "items": items if items is not None else [_item()],
    }


def test_valid_registry_passes():
    validate_registry(_registry())


def test_query_returns_item_and_unknown_raises():
    reg = _registry(items=[_item("s1"), _item("s2")])
    assert query(reg, "s2")["id"] == "s2"
    with pytest.raises(RegistryError, match="unknown source"):
        query(reg, "ghost")


def test_assert_use_allowed_ok_returns_item():
    reg = _registry(items=[_item("s1", allowed=["query_only", "human_research"])])
    item = assert_use_allowed(reg, "s1", "query_only")
    assert item["id"] == "s1"


def test_assert_use_prohibited_raises():
    reg = _registry(items=[_item("s1", allowed=["query_only"],
                                  prohibited=["model_training"])])
    with pytest.raises(RegistryError, match="PROHIBITED"):
        assert_use_allowed(reg, "s1", "model_training")


def test_assert_use_not_in_allowed_raises():
    reg = _registry(items=[_item("s1", allowed=["query_only"])])
    with pytest.raises(RegistryError, match="not in allowed_uses"):
        assert_use_allowed(reg, "s1", "evidence_citation")


def test_assert_use_unknown_use_raises():
    reg = _registry(items=[_item("s1")])
    with pytest.raises(RegistryError, match="unknown use"):
        assert_use_allowed(reg, "s1", "telepathy")


def test_duplicate_provider_id_rejected():
    reg = _registry(providers=[{"id": "p1", "name": "A"}, {"id": "p1", "name": "B"}])
    with pytest.raises(RegistryError, match="duplicate provider id"):
        validate_registry(reg)


def test_duplicate_item_id_rejected():
    reg = _registry(items=[_item("dup"), _item("dup")])
    with pytest.raises(RegistryError, match="duplicate item id"):
        validate_registry(reg)


def test_item_unknown_provider_rejected():
    reg = _registry(items=[_item("s1", provider="ghost")])
    with pytest.raises(RegistryError, match="unknown provider"):
        validate_registry(reg)


def test_allowed_use_outside_vocabulary_rejected():
    reg = _registry(items=[_item("s1", allowed=["query_only", "mind_meld"])])
    with pytest.raises(RegistryError, match="outside the vocabulary"):
        validate_registry(reg)


def test_allowed_prohibited_overlap_rejected():
    reg = _registry(items=[_item("s1", allowed=["query_only"],
                                  prohibited=["query_only"])])
    with pytest.raises(RegistryError, match="both allowed and prohibited"):
        validate_registry(reg)


def test_unknown_rights_status_rejected():
    reg = _registry(items=[_item("s1", rights="totally_fine_trust_me")])
    with pytest.raises(RegistryError, match="unknown rights_status"):
        validate_registry(reg)


def test_ingested_requires_checksum():
    reg = _registry(items=[_item("s1", rights="cleared", ingested=True)])
    with pytest.raises(RegistryError, match="no checksum"):
        validate_registry(reg)


def test_ingested_requires_cleared_rights():
    reg = _registry(items=[_item("s1", rights="pending_review", ingested=True,
                                 checksum="abc123")])
    with pytest.raises(RegistryError, match="not 'cleared'"):
        validate_registry(reg)


def test_ingested_cleared_with_checksum_passes():
    reg = _registry(items=[_item("s1", rights="cleared", ingested=True,
                                 checksum="abc123",
                                 allowed=["evidence_citation"])])
    validate_registry(reg)  # no raise


def test_uses_requiring_ingestion_membership():
    assert "evidence_citation" in USES_REQUIRING_INGESTION
    assert "model_training" in USES_REQUIRING_INGESTION
    assert "query_only" not in USES_REQUIRING_INGESTION
    assert "human_research" not in USES_REQUIRING_INGESTION


def test_empty_vocabulary_rejected():
    reg = _registry(vocab=[])
    with pytest.raises(RegistryError, match="non-empty list"):
        validate_registry(reg)


def test_load_registry_missing_file(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "nope.yaml")


def test_load_registry_bad_yaml(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text("a: [b: c: d", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid YAML"):
        load_registry(p)
