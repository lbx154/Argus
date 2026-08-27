"""Fiction's reference artifact producer emits a coherent lineage chain."""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.artifacts import (
    FICTION_ARTIFACT_KINDS,
    build_fiction_manifest,
)
from argus_skill.verticals.literary.shared.artifact_manifest import lineage

# --------------------------------------------------------------------------- #
# producer: the canonical fiction chain is valid, traced, and versioned
# --------------------------------------------------------------------------- #

def test_build_fiction_manifest_is_valid_under_fiction_vocab():
    m = build_fiction_manifest("fic-1")
    assert m["task_id"] == "fic-1"
    assert len(m["artifacts"]) == 11
    assert {a["kind"] for a in m["artifacts"]} <= FICTION_ARTIFACT_KINDS


def test_final_traces_back_to_draft_and_review():
    m = build_fiction_manifest("fic-1")
    by_id = {a["artifact_id"]: a for a in m["artifacts"]}
    ancestor_kinds = {by_id[i]["kind"] for i in lineage(m, "final")}
    assert {"draft", "review"} <= ancestor_kinds


def test_supersede_bookkeeping_is_coherent():
    m = build_fiction_manifest("fic-1")
    by_id = {a["artifact_id"]: a for a in m["artifacts"]}
    assert by_id["draft"]["status"] == "superseded"
    assert by_id["final"]["supersedes"] == "draft"
    assert by_id["final"]["status"] == "final"
    assert by_id["state"]["status"] == "superseded"
    assert by_id["final_state"]["supersedes"] == "state"
