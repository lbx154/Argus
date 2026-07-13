"""literary_editor runtime capstone: registered + all four shared contracts + the
edit-discipline gate enforce at RUN TIME via STAGE_CHECKS (subprocess).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals.literary_editor.artifacts import build_editor_manifest
from argus_skill.verticals.literary_editor.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = "这是一段需要校对的文字，里面藏着一个关键句，还有个错别字。"


def _all_cmds():
    return [cmd for checks in STAGE_CHECKS.values() for _d, cmd in checks]


def _cmd(needle: str) -> str:
    hits = [c for c in _all_cmds() if needle in c]
    assert hits, f"no STAGE_CHECK wires {needle!r}"
    return hits[0]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                          text=True, env=env)


def _write(base: Path, rel: str, obj) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj,
                 encoding="utf-8")


def test_vertical_is_registered():
    assert "literary_editor" in VERTICALS


def test_all_four_contracts_plus_edit_are_wired():
    assert _cmd("intake-validate")
    assert _cmd("review-validate")
    assert _cmd("check-plan")
    assert _cmd("manifest-validate")
    assert _cmd("manifest-content")
    assert _cmd("source-registry")
    assert _cmd("check-usage")
    assert _cmd("edit-check editor/source.txt")


def test_runtime_edit_gate_passes_disciplined_fails_violation(tmp_path):
    cmd = _cmd("edit-check editor/source.txt")
    _write(tmp_path, "editor/source.txt", _SRC)
    _write(tmp_path, "editor/edited.txt",
           "这是一段需要校对的文字，里面藏着一个关键句，还有一个错别字。")
    _write(tmp_path, "editor/edit_brief.json",
           {"mode": "proofread", "must_keep": ["关键句"]})
    assert _run(cmd, tmp_path).returncode == 0
    # a critique that rewrote the source -> mode discipline violated -> FAIL
    _write(tmp_path, "editor/edited.txt", "完全改写成另一段话。")
    _write(tmp_path, "editor/edit_brief.json", {"mode": "critique"})
    assert _run(cmd, tmp_path).returncode != 0
    # dropping a must-keep segment -> FAIL
    _write(tmp_path, "editor/edited.txt", "改写后的文字，把该保留的丢了。")
    _write(tmp_path, "editor/edit_brief.json",
           {"mode": "rewrite", "must_keep": ["关键句"]})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_intake_gate(tmp_path):
    cmd = _cmd("intake-validate")
    _write(tmp_path, "editor/task_envelope.json",
           {"task_id": "e1", "mode": "polish", "language": "zh",
            "form": "short_story", "intent": "润色",
            "reference_inputs": [{"role": "source_text", "ref": "draft.md"}]})
    assert _run(cmd, tmp_path).returncode == 0
    # from_scratch is not an editing mode -> reject
    _write(tmp_path, "editor/task_envelope.json",
           {"task_id": "e1", "mode": "from_scratch", "language": "zh",
            "form": "short_story", "intent": "写一篇"})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_registry_review_usage_manifest(tmp_path):
    assert _run(_cmd("source-registry"), tmp_path).returncode == 0

    rv = _cmd("review-validate")
    _write(tmp_path, "editor/review.json", {"verdict": "revise", "findings": [
        {"id": "f1", "type": "fact_fidelity", "severity": "major", "blocking": False,
         "location": "p1", "evidence": "invented a date", "suggested_action": "remove"}]})
    assert _run(rv, tmp_path).returncode == 0

    cu = _cmd("check-usage")
    _write(tmp_path, "editor/source_usage.json", {"task_id": "e1", "uses": []})
    assert _run(cu, tmp_path).returncode == 0
    _write(tmp_path, "editor/source_usage.json", {"task_id": "e1", "uses": [
        {"use_id": "u1", "source_id": "ghost", "use": "query_only",
         "stage": "edit", "consumed_by": "edited"}]})
    assert _run(cu, tmp_path).returncode != 0

    mv = _cmd("manifest-validate")
    _write(tmp_path, "editor/artifact_manifest.json", build_editor_manifest("e1"))
    assert _run(mv, tmp_path).returncode == 0
