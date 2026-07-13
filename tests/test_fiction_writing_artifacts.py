"""Closed-loop tests: fiction produces a valid artifact manifest AND the revise
STAGE_CHECKS enforce it at run time.

The first half proves the reference producer (:func:`build_fiction_manifest`)
emits a chain that validates under fiction's kind vocabulary with correct
provenance and supersede bookkeeping. The second half runs the actual revise
STAGE_CHECKS commands as subprocesses (exactly as argus_skill.tools.stage_check
does) — proving the contract is CONSUMED by the runtime gate, not only by unit
tests. If the wiring in stages.py is removed, the `_checks_with` lookups return
[] and these tests fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.literary.artifact_manifest import lineage
from argus_skill.verticals.fiction_writing.artifacts import (
    FICTION_ARTIFACT_KINDS,
    build_fiction_manifest,
)
from argus_skill.verticals.fiction_writing.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]


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


# --------------------------------------------------------------------------- #
# runtime: the revise STAGE_CHECKS actually gate on the manifest contract
# --------------------------------------------------------------------------- #

def _checks_with(stage: str, needle: str) -> list[str]:
    return [cmd for _desc, cmd in STAGE_CHECKS[stage] if needle in cmd]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, env=env)


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _materialize(base: Path, manifest: dict) -> None:
    """Write the manifest plus a non-empty file for every artifact it records."""
    _write(base / "fiction" / "artifact_manifest.json", manifest)
    for a in manifest["artifacts"]:
        p = base / a["content_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")


def test_revise_stage_wires_the_manifest_checks():
    assert _checks_with("revise", "manifest_check validate")
    assert _checks_with("revise", "manifest_check check-content")
    assert _checks_with("revise", "manifest_check check-lineage")


def test_runtime_manifest_gate_passes_valid_fails_dangling(tmp_path):
    cmd = _checks_with("revise", "manifest_check validate")[0]
    m = build_fiction_manifest("fic-1")
    _materialize(tmp_path, m)
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # a dangling parent must FAIL the gate
    m_bad = build_fiction_manifest("fic-1")
    m_bad["artifacts"][3]["parent_artifact_ids"] = ["ghost"]
    _write(tmp_path / "fiction" / "artifact_manifest.json", m_bad)
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0


def test_runtime_content_gate_fails_when_a_file_is_missing(tmp_path):
    cmd = _checks_with("revise", "manifest_check check-content")[0]
    m = build_fiction_manifest("fic-1")
    _materialize(tmp_path, m)
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # delete one recorded artifact's file -> content gate fails
    (tmp_path / "fiction" / "final.md").unlink()
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0


def test_runtime_lineage_gate_fails_without_review_provenance(tmp_path):
    cmd = _checks_with("revise", "manifest_check check-lineage")[0]
    m = build_fiction_manifest("fic-1")
    _materialize(tmp_path, m)
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # a valid manifest whose final does NOT descend from a review -> lineage fails
    no_review = {
        "task_id": "x",
        "artifacts": [
            {"artifact_id": "brief", "kind": "creative_brief", "version": 1,
             "producer_stage": "intake",
             "content_path": "fiction/creative_brief.json", "status": "active"},
            {"artifact_id": "draft", "kind": "draft", "version": 1,
             "producer_stage": "draft", "content_path": "fiction/draft.md",
             "parent_artifact_ids": ["brief"], "status": "superseded"},
            {"artifact_id": "final", "kind": "final", "version": 2,
             "producer_stage": "revise", "content_path": "fiction/final.md",
             "parent_artifact_ids": ["brief", "draft"], "supersedes": "draft",
             "status": "final"},
        ],
    }
    _write(tmp_path / "fiction" / "artifact_manifest.json", no_review)
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0
