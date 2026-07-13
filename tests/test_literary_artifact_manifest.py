"""Closed-loop tests for the shared literary Artifact Manifest contract.

Covers the full lineage bookkeeping — normalize/validate, the provenance query,
runtime content presence — plus every rejection the acceptance definition
requires: duplicate id, dangling parent/supersedes, self-reference, lineage
cycle, supersede<->superseded incoherence (both directions), unknown kind, and
the structural schema violations. No test merely asserts the schema loads.
"""
from __future__ import annotations

import copy

import pytest

from argus_skill.literary.artifact_manifest import (
    ManifestError,
    assert_content_present,
    lineage,
    normalize_manifest,
    validate_manifest,
)


def _artifact(aid, kind="draft", stage="draft", path=None, **kw):
    a = {
        "artifact_id": aid,
        "kind": kind,
        "version": 1,
        "producer_stage": stage,
        "content_path": path or f"fiction/{aid}.json",
        "status": "active",
    }
    a.update(kw)
    return a


def _manifest(*artifacts, task_id="t1"):
    return {"task_id": task_id, "artifacts": [copy.deepcopy(a) for a in artifacts]}


# A minimal but real chain: brief -> draft, then final supersedes draft.
VALID = _manifest(
    _artifact("brief", kind="creative_brief", stage="intake"),
    _artifact("draft", kind="draft", parent_artifact_ids=["brief"],
              status="superseded"),
    _artifact("final", kind="final", version=2, stage="revise",
              parent_artifact_ids=["brief", "draft"], supersedes="draft",
              status="final"),
)


def test_valid_manifest_normalizes_and_validates():
    m = normalize_manifest(VALID)
    assert len(m["artifacts"]) == 3
    # optional fields defaulted on the leaf that omitted them
    brief = next(a for a in m["artifacts"] if a["artifact_id"] == "brief")
    assert brief["parent_artifact_ids"] == []
    assert brief["supersedes"] is None
    assert brief["constraint_refs"] == [] and brief["source_refs"] == []


def test_normalize_does_not_mutate_input():
    raw = copy.deepcopy(VALID)
    normalize_manifest(raw)
    assert raw == VALID  # inputs are left untouched


def test_duplicate_artifact_id_rejected():
    bad = _manifest(_artifact("dup", kind="creative_brief", stage="intake"),
                    _artifact("dup"))
    with pytest.raises(ManifestError, match="duplicate artifact_id"):
        normalize_manifest(bad)


def test_dangling_parent_rejected():
    bad = _manifest(_artifact("a", kind="draft", parent_artifact_ids=["ghost"]))
    with pytest.raises(ManifestError, match="unknown parent"):
        normalize_manifest(bad)


def test_self_parent_rejected():
    bad = _manifest(_artifact("a", parent_artifact_ids=["a"]))
    with pytest.raises(ManifestError, match="itself as a parent"):
        normalize_manifest(bad)


def test_dangling_supersedes_rejected():
    bad = _manifest(_artifact("a", supersedes="ghost"))
    with pytest.raises(ManifestError, match="supersedes unknown artifact"):
        normalize_manifest(bad)


def test_self_supersedes_rejected():
    bad = _manifest(_artifact("a", supersedes="a"))
    with pytest.raises(ManifestError, match="supersedes itself"):
        normalize_manifest(bad)


def test_supersedes_target_must_be_marked_superseded():
    # forward coherence: draft is still 'active' yet final claims to replace it
    bad = _manifest(
        _artifact("draft", kind="draft", status="active"),
        _artifact("final", kind="final", supersedes="draft", status="final"),
    )
    with pytest.raises(ManifestError, match="not 'superseded'"):
        normalize_manifest(bad)


def test_superseded_without_successor_rejected():
    # backward coherence: marked superseded but nothing replaces it
    bad = _manifest(_artifact("draft", kind="draft", status="superseded"))
    with pytest.raises(ManifestError, match="no artifact supersedes it"):
        normalize_manifest(bad)


def test_superseded_by_two_rejected():
    bad = _manifest(
        _artifact("t", kind="draft", status="superseded"),
        _artifact("x", kind="final", supersedes="t"),
        _artifact("y", kind="final", supersedes="t"),
    )
    with pytest.raises(ManifestError, match="more than one"):
        normalize_manifest(bad)


def test_lineage_cycle_rejected():
    bad = _manifest(
        _artifact("a", parent_artifact_ids=["b"]),
        _artifact("b", parent_artifact_ids=["a"]),
    )
    with pytest.raises(ManifestError, match="cycle"):
        normalize_manifest(bad)


def test_unknown_kind_rejected_with_vocab_accepted_without():
    m = _manifest(_artifact("a", kind="mystery", stage="intake"))
    # no vocabulary supplied -> any non-empty kind accepted
    normalize_manifest(m)
    # vocabulary supplied -> unknown kind rejected
    with pytest.raises(ManifestError, match="unknown kind"):
        normalize_manifest(m, kind_vocabulary={"creative_brief", "draft"})


def test_lineage_is_transitive():
    m = normalize_manifest(VALID)
    assert set(lineage(m, "final")) == {"brief", "draft"}
    assert lineage(m, "brief") == []
    with pytest.raises(ManifestError, match="unknown artifact_id"):
        lineage(m, "nope")


def test_content_presence_pass_and_fail(tmp_path):
    m = normalize_manifest(VALID)
    for a in m["artifacts"]:
        p = tmp_path / a["content_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert_content_present(m, tmp_path)  # all present -> no raise

    # a missing file fails
    (tmp_path / "fiction" / "final.json").unlink()
    with pytest.raises(ManifestError, match="not found"):
        assert_content_present(m, tmp_path)

    # an empty file fails
    (tmp_path / "fiction" / "final.json").write_text("", encoding="utf-8")
    with pytest.raises(ManifestError, match="is empty"):
        assert_content_present(m, tmp_path)


@pytest.mark.parametrize("mutate, match", [
    (lambda m: m["artifacts"][0].pop("kind"), "invalid artifact_manifest"),
    (lambda m: m["artifacts"][0].__setitem__("version", 0), "invalid artifact_manifest"),
    (lambda m: m["artifacts"][0].__setitem__("status", "bogus"), "invalid artifact_manifest"),
    (lambda m: m["artifacts"][0].__setitem__("stray", 1), "invalid artifact_manifest"),
    (lambda m: m.__setitem__("task_id", ""), "invalid artifact_manifest"),
])
def test_structural_schema_violations_rejected(mutate, match):
    bad = copy.deepcopy(VALID)
    mutate(bad)
    with pytest.raises(ManifestError, match=match):
        validate_manifest(bad)
