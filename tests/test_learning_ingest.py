"""Learning input channel: material ingest (immutable source + audit manifest)
and the `learn` CLI that stages material and persists the learning vertical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from argus_skill.apps.cli._core import _cmd_learn
from argus_skill.verticals.learning.ingest import ingest_material
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.schema import SourceNote
from argus_skill.wiki.store import WikiStore

_TEXT = "GRPO clips the ratio asymmetrically for training stability.\n"


@pytest.fixture
def wiki_root(tmp_path):
    return init_wiki("learning", base=tmp_path)


# --------------------------------------------------------------------------- #
# ingest_material
# --------------------------------------------------------------------------- #
def test_ingest_plaintext_writes_immutable_source_and_manifest(wiki_root, tmp_path):
    mat = tmp_path / "grpo-tricks.md"
    mat.write_text(_TEXT, encoding="utf-8")
    store = WikiStore(wiki_root)

    manifest = ingest_material(mat, store, ingested_by="test@x", today=date(2026, 7, 4))
    assert manifest["written"] is True
    assert manifest["source_id"] == "grpo-tricks"
    assert manifest["extractor"] == "plaintext"
    assert manifest["char_count"] == len(_TEXT)
    assert manifest["sha256"] == hashlib.sha256(mat.read_bytes()).hexdigest()

    # the source is on disk and readable, with the verbatim body preserved
    loaded = store.read_source(SourceNote, "grpo-tricks")
    assert "clips the ratio asymmetrically" in loaded.body


def test_ingest_reingest_is_benign_noop(wiki_root, tmp_path):
    mat = tmp_path / "dup.txt"
    mat.write_text(_TEXT, encoding="utf-8")
    store = WikiStore(wiki_root)
    assert ingest_material(mat, store)["written"] is True
    # sources are immutable -> second ingest does not overwrite, reports written=False
    assert ingest_material(mat, store)["written"] is False


def test_ingest_unsupported_format_raises(wiki_root, tmp_path):
    mat = tmp_path / "thing.bin"
    mat.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        ingest_material(mat, WikiStore(wiki_root))


# --------------------------------------------------------------------------- #
# learn CLI
# --------------------------------------------------------------------------- #
def test_learn_cmd_stages_material_and_persists_vertical(tmp_path, capsys):
    mat = tmp_path / "material.md"
    mat.write_text(_TEXT, encoding="utf-8")
    args = argparse.Namespace(
        material=[mat], project="learning", base=tmp_path, ingested_by="test@x",
    )
    rc = _cmd_learn(args)
    assert rc == 0

    # immutable source written under the learning wiki
    wiki_root = tmp_path / ".autors" / "learning" / "wiki"
    assert (wiki_root / "sources" / "notes" / "material.md").exists()

    # material manifest the ingest stage-check looks for
    manifest = json.loads((tmp_path / "learning" / "MATERIAL_MANIFEST.json").read_text())
    assert manifest["materials"][0]["source_id"] == "material"

    # vertical persisted to learning
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "learning"


def test_learn_cmd_missing_material_errors(tmp_path):
    args = argparse.Namespace(
        material=[tmp_path / "nope.md"], project="learning", base=tmp_path,
        ingested_by="test@x",
    )
    assert _cmd_learn(args) == 2
