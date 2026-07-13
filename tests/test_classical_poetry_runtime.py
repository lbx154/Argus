"""classical_poetry runtime capstone: registered, and all four shared contracts +
the machine prosody gate enforce at RUN TIME via STAGE_CHECKS (subprocess).

Proves poetry is a real second consumer of the shared foundation — not a private
module — and that the crown prosody gate fails a bad poem at the stage, not just
in a unit test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals.classical_poetry.artifacts import build_poetry_manifest
from argus_skill.verticals.classical_poetry.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DENG = "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"


def _all_cmds():
    return [cmd for checks in STAGE_CHECKS.values() for _d, cmd in checks]


def _cmd(needle: str) -> str:
    hits = [c for c in _all_cmds() if needle in c]
    assert hits, f"no STAGE_CHECK wires {needle!r}"
    return hits[0]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, env=env)


def _write(base: Path, rel: str, obj) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj,
                 encoding="utf-8")


# --------------------------------------------------------------------------- #
# registration + all four contracts wired
# --------------------------------------------------------------------------- #

def test_vertical_is_registered():
    assert "classical_poetry" in VERTICALS


def test_all_four_contracts_plus_prosody_are_wired():
    assert _cmd("intake-validate")        # Task Envelope
    assert _cmd("review-validate")        # Review
    assert _cmd("check-plan")
    assert _cmd("manifest-validate")      # Artifact
    assert _cmd("manifest-content")
    assert _cmd("source-registry")        # Provenance
    assert _cmd("check-usage")
    assert _cmd("prosody poetry/draft_poem.txt")  # crown machine gate


# --------------------------------------------------------------------------- #
# runtime: the prosody gate is the crown — compliant passes, broken fails
# --------------------------------------------------------------------------- #

def test_runtime_prosody_gate_passes_compliant_fails_broken(tmp_path):
    cmd = _cmd("prosody poetry/draft_poem.txt")
    _write(tmp_path, "poetry/draft_poem.txt", _DENG)
    assert _run(cmd, tmp_path).returncode == 0
    # 出韵 + 三平尾 -> the stage FAILS
    _write(tmp_path, "poetry/draft_poem.txt",
           "白日依山高，黄河入海天。欲穷千里目，更上一层云。")
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_intake_gate(tmp_path):
    cmd = _cmd("intake-validate")
    _write(tmp_path, "poetry/task_envelope.json",
           {"task_id": "p1", "mode": "from_scratch", "language": "zh",
            "form": "五言绝句", "intent": "写一首五绝"})
    assert _run(cmd, tmp_path).returncode == 0
    # non-zh -> rejected
    _write(tmp_path, "poetry/task_envelope.json",
           {"task_id": "p1", "mode": "from_scratch", "language": "en",
            "form": "五言绝句", "intent": "x"})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_source_registry_valid(tmp_path):
    assert _run(_cmd("source-registry"), tmp_path).returncode == 0


def test_runtime_review_gate(tmp_path):
    cmd = _cmd("review-validate")
    good = {"verdict": "revise", "findings": [
        {"id": "f1", "type": "rhyme", "severity": "critical", "blocking": True,
         "location": "第2句", "evidence": "出韵", "suggested_action": "改韵脚"}]}
    _write(tmp_path, "poetry/review.json", good)
    assert _run(cmd, tmp_path).returncode == 0
    # a finding type outside poetry's vocabulary is rejected
    bad = {"verdict": "revise", "findings": [
        {"id": "f1", "type": "plot_hole", "severity": "critical", "blocking": True,
         "location": "x", "evidence": "y", "suggested_action": "z"}]}
    _write(tmp_path, "poetry/review.json", bad)
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_usage_gate(tmp_path):
    cmd = _cmd("check-usage")
    _write(tmp_path, "poetry/source_usage.json", {"task_id": "p1", "uses": []})
    assert _run(cmd, tmp_path).returncode == 0
    # unregistered source -> fail
    _write(tmp_path, "poetry/source_usage.json", {"task_id": "p1", "uses": [
        {"use_id": "u1", "source_id": "ghost", "use": "query_only",
         "stage": "compose", "consumed_by": "draft"}]})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_manifest_gate(tmp_path):
    cmd = _cmd("manifest-validate")
    _write(tmp_path, "poetry/artifact_manifest.json", build_poetry_manifest("p1"))
    assert _run(cmd, tmp_path).returncode == 0
    # dangling parent -> fail
    m = build_poetry_manifest("p1")
    m["artifacts"][3]["parent_artifact_ids"] = ["ghost"]
    _write(tmp_path, "poetry/artifact_manifest.json", m)
    assert _run(cmd, tmp_path).returncode != 0
