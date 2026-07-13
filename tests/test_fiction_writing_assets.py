"""Deterministic guards for the fiction_writing static assets:
- #5 integrity: every schema is a valid Draft-07 schema, every skill md parses
  via the real Skill parser, every JSON file loads;
- #6 source registry: two-layer (providers + items), allowed_uses drawn from the
  controlled vocabulary, item.provider references resolve, and nothing is marked
  `ingested` without a checksum.
No network, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

import argus_skill.verticals.fiction_writing as fw
from argus_skill.skills.store import Skill

_FW = Path(fw.__file__).resolve().parent


# ---- #5 integrity ---------------------------------------------------------- #
def test_all_json_files_parse():
    for p in _FW.rglob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))  # raises on corruption


def test_schemas_are_valid_draft7():
    for name in ("story_state.schema.json", "state_patch.schema.json"):
        schema = json.loads((_FW / "schemas" / name).read_text(encoding="utf-8"))
        Draft7Validator.check_schema(schema)


def test_all_skills_parse_with_real_parser():
    md = sorted((_FW / "skills").rglob("*.md"))
    assert len(md) >= 6
    for p in md:
        sk = Skill.parse(p.read_text(encoding="utf-8"), str(p))
        assert sk.name and sk.description and sk.category
        body = sk.content.lower()
        assert "when to use" in body and "how to solve" in body


# ---- #6 source registry ---------------------------------------------------- #
def _registry():
    path = _FW / "references" / "source_registry" / "sources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_registry_is_two_layer_with_controlled_uses():
    reg = _registry()
    vocab = set(reg["allowed_use_vocabulary"])
    assert vocab == {
        "query_only", "human_research", "evidence_citation",
        "local_indexing", "model_training", "redistribution",
    }
    provider_ids = {p["id"] for p in reg["providers"]}
    assert provider_ids, "no providers"
    for prov in reg["providers"]:
        assert {"id", "access_mode", "provider_terms_reviewed"} <= prov.keys()
    for item in reg["items"]:
        assert {"id", "provider", "rights_status", "allowed_uses"} <= item.keys()
        assert item["provider"] in provider_ids, f"{item['id']}: unknown provider"
        assert set(item["allowed_uses"]) <= vocab, f"{item['id']}: use not in vocab"
        # honesty guard: cannot claim ingested without a checksum
        if item.get("ingested"):
            assert item.get("checksum"), f"{item['id']}: ingested without checksum"


def test_registry_nothing_ingested_yet_and_corpora_are_query_only():
    reg = _registry()
    assert all(not it.get("ingested") for it in reg["items"]), "unexpected ingested item"
    by_id = {it["id"]: it for it in reg["items"]}
    for cid in ("bcc_query_access", "coca_query_access"):
        # naturalness corpora must NOT permit training/redistribution/indexing
        allowed = set(by_id[cid]["allowed_uses"])
        assert "model_training" not in allowed
        assert "redistribution" not in allowed
        assert "local_indexing" not in allowed
