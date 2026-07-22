"""Closed-loop tests for the shared literary Provenance (source-usage) contract.

Proves the usage ledger is cross-checked against the registry, not merely
schema-valid: an unregistered source, a use the source's rights forbid, a
citation without attribution, and — the honesty guard — a queried-but-not-ingested
source used as if it were cited/indexed/trained all fail. An empty ledger is a
valid, explicit "no external sources" declaration.
"""
from __future__ import annotations

import copy

import pytest

from argus_skill.verticals.literary.shared.provenance import (
    ProvenanceError,
    normalize_usage,
    validate_usage,
)

_VOCAB = ["query_only", "human_research", "evidence_citation",
          "local_indexing", "model_training", "redistribution"]

# A valid registry with three shapes: query-only (not ingested), citable+ingested,
# and citable-but-NOT-ingested (to isolate the ingestion guard).
_REGISTRY = {
    "allowed_use_vocabulary": _VOCAB,
    "providers": [{"id": "p1", "name": "P1"}],
    "items": [
        {"id": "q1", "provider": "p1", "rights_status": "pending_review",
         "allowed_uses": ["query_only", "human_research"],
         "prohibited_uses": ["model_training"], "ingested": False},
        {"id": "c1", "provider": "p1", "rights_status": "cleared",
         "allowed_uses": ["evidence_citation", "human_research"],
         "ingested": True, "checksum": "deadbeef"},
        {"id": "nc", "provider": "p1", "rights_status": "pending_review",
         "allowed_uses": ["evidence_citation", "human_research"],
         "ingested": False},
    ],
}


def _use(uid="u1", source_id="q1", use="query_only", **kw):
    u = {"use_id": uid, "source_id": source_id, "use": use,
         "stage": "draft", "consumed_by": "draft"}
    u.update(kw)
    return u


def _usage(*uses, task_id="t1"):
    return {"task_id": task_id, "uses": list(uses)}


def test_valid_usage_passes():
    validate_usage(_usage(_use()), _REGISTRY)


def test_empty_ledger_is_explicit_no_sources_and_passes():
    validate_usage(_usage(), _REGISTRY)


def test_normalize_fills_defaults_without_mutating_input():
    raw = _usage(_use())
    snapshot = copy.deepcopy(raw)
    norm = normalize_usage(raw, _REGISTRY)
    assert norm["uses"][0]["citation"] == ""
    assert norm["uses"][0]["note"] == ""
    assert raw == snapshot


def test_duplicate_use_id_rejected():
    bad = _usage(_use("dup"), _use("dup", source_id="c1", use="human_research"))
    with pytest.raises(ProvenanceError, match="duplicate use_id"):
        validate_usage(bad, _REGISTRY)


def test_unknown_source_rejected():
    with pytest.raises(ProvenanceError, match="unknown source"):
        validate_usage(_usage(_use(source_id="ghost")), _REGISTRY)


def test_disallowed_use_rejected():
    with pytest.raises(ProvenanceError, match="not in allowed_uses"):
        validate_usage(_usage(_use(source_id="q1", use="evidence_citation")), _REGISTRY)


def test_prohibited_use_rejected():
    with pytest.raises(ProvenanceError, match="PROHIBITED"):
        validate_usage(_usage(_use(source_id="q1", use="model_training")), _REGISTRY)


def test_evidence_citation_requires_attribution():
    # c1 allows evidence_citation and is ingested, but no citation is given
    with pytest.raises(ProvenanceError, match="carries no citation"):
        validate_usage(_usage(_use(source_id="c1", use="evidence_citation")), _REGISTRY)


def test_evidence_citation_on_non_ingested_source_rejected():
    # nc ALLOWS evidence_citation but is not ingested -> the honesty guard fires
    use = _use(source_id="nc", use="evidence_citation", citation="Author, 1900")
    with pytest.raises(ProvenanceError, match="not ingested"):
        validate_usage(_usage(use), _REGISTRY)


def test_evidence_citation_on_ingested_source_with_attribution_passes():
    use = _use(source_id="c1", use="evidence_citation", citation="Author, Title, p.12")
    validate_usage(_usage(use), _REGISTRY)


@pytest.mark.parametrize("mutate, match", [
    (lambda u: u["uses"][0].pop("source_id"), "invalid source_usage"),
    (lambda u: u["uses"][0].__setitem__("use", "telepathy"), "invalid source_usage"),
    (lambda u: u["uses"][0].__setitem__("stray", 1), "invalid source_usage"),
    (lambda u: u.__setitem__("task_id", ""), "invalid source_usage"),
])
def test_structural_schema_violations_rejected(mutate, match):
    bad = _usage(_use())
    mutate(bad)
    with pytest.raises(ProvenanceError, match=match):
        validate_usage(bad, _REGISTRY)
