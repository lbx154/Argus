"""Tests for the corpus-ingestion scaffold: the contract has teeth (abstract-only,
bound rights), and the pipeline is honest (blocked without authorized sources,
fabricates nothing)."""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.ingest import (
    CraftCardError,
    distill_card,
    plan_ingestion,
    validate_craft_card,
)

_GOOD = {
    "id": "cc_jie_qing_yu_jing",
    "title": "以景结情",
    "technique": "在情绪高点后收束于一个外部景物，让余味替代直陈心理。",
    "language": "zh",
    "applicable_genres": ["classical_zhanghui", "literary"],
    "abstracted": True,
    "evidence": [{"source_id": "hlm_gutenberg", "locator": "第27回 末段",
                  "note": "以落花/风声收束黛玉葬花之悲，不直言其心。"}],
    "rights": {"source_id": "hlm_gutenberg", "allowed_use": "public-domain study"},
}


def test_valid_abstract_card_passes():
    validate_craft_card(_GOOD)  # no raise


def test_non_abstracted_card_is_rejected():
    bad = {**_GOOD, "abstracted": False}
    with pytest.raises(CraftCardError):
        validate_craft_card(bad)


def test_card_without_rights_is_rejected():
    bad = {k: v for k, v in _GOOD.items() if k != "rights"}
    with pytest.raises(CraftCardError):
        validate_craft_card(bad)


def test_evidence_note_length_capped_blocks_liftable_passage():
    # the note cap is the anti-copy boundary — a whole lifted passage cannot hide here
    bad = {**_GOOD, "evidence": [{"source_id": "s", "locator": "l", "note": "字" * 201}]}
    with pytest.raises(CraftCardError):
        validate_craft_card(bad)


def test_plan_blocked_without_authorized_sources():
    for empty in (None, []):
        out = plan_ingestion(empty)
        assert out["blocked"] is True and "fabricate" in out["reason"]


def test_plan_orders_authorized_sources_and_marks_retrieval_only():
    out = plan_ingestion([
        {"source_id": "hlm", "kind": "public_domain_study"},
        {"source_id": "bcc", "kind": "modern_corpus_retrieval"},
    ])
    assert out["blocked"] is False
    actions = {s["source_id"]: s["action"] for s in out["steps"]}
    assert actions["hlm"] == "distill_craft_cards"
    assert actions["bcc"] == "retrieval-only"   # licensed corpus never ingested verbatim


def test_distill_validates_injected_output_and_never_fabricates():
    # a distiller that returns junk must be rejected (fabrication can't slip through)
    with pytest.raises(CraftCardError):
        distill_card([{"source_id": "s"}], lambda ev: {"id": "x"})
    # a distiller returning a valid card round-trips
    assert distill_card([{"source_id": "s"}], lambda ev: _GOOD) == _GOOD
