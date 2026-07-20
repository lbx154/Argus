"""modern_poetry runtime capstone: registered + all four shared contracts + the
form-check gate enforce at RUN TIME via STAGE_CHECKS (subprocess).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals.modern_poetry.artifacts import build_modern_manifest
from argus_skill.verticals.modern_poetry.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_POEM = "夜把城市折起来\n只留一盏灯\n和灯下没说完的话"


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
    assert "modern_poetry" in VERTICALS


def test_all_four_contracts_plus_form_are_wired():
    assert _cmd("intake-validate")
    assert _cmd("review-validate")
    assert _cmd("check-plan")
    assert _cmd("manifest-validate")
    assert _cmd("manifest-content")
    assert _cmd("source-registry")
    assert _cmd("check-usage")
    assert _cmd("form-check poetry/draft_poem.txt")


def test_runtime_form_gate_passes_compliant_fails_banned(tmp_path):
    cmd = _cmd("form-check poetry/draft_poem.txt")
    _write(tmp_path, "poetry/draft_poem.txt", _POEM)
    _write(tmp_path, "poetry/form_spec.json", {"language": "zh", "line_count": 3})
    assert _run(cmd, tmp_path).returncode == 0
    # a banned cliché present -> the stage FAILS
    _write(tmp_path, "poetry/form_spec.json",
           {"language": "zh", "banned_words": ["灯下"]})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_form_gate_rejects_missing_or_malformed_declared_spec(tmp_path):
    cmd = _cmd("form-check poetry/draft_poem.txt")
    _write(tmp_path, "poetry/draft_poem.txt", _POEM)
    assert _run(cmd, tmp_path).returncode != 0
    _write(tmp_path, "poetry/form_spec.json", "{not-json")
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_intake_gate(tmp_path):
    cmd = _cmd("intake-validate")
    _write(tmp_path, "poetry/task_envelope.json",
           {"task_id": "m1", "mode": "from_scratch", "language": "zh",
            "form": "free_verse", "intent": "写一首自由诗"})
    assert _run(cmd, tmp_path).returncode == 0
    _write(tmp_path, "poetry/task_envelope.json",
           {"task_id": "m1", "mode": "from_scratch", "language": "zh",
            "form": "五言绝句", "intent": "x"})
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_source_registry_valid(tmp_path):
    assert _run(_cmd("source-registry"), tmp_path).returncode == 0


def test_runtime_review_and_usage_and_manifest(tmp_path):
    rv = _cmd("review-validate")
    _write(tmp_path, "poetry/review.json", {"verdict": "revise", "findings": [
        {"id": "f1", "type": "imagery", "severity": "minor", "blocking": False,
         "location": "l2", "evidence": "flat", "suggested_action": "sharpen"}]})
    assert _run(rv, tmp_path).returncode == 0

    cu = _cmd("check-usage")
    _write(tmp_path, "poetry/source_usage.json", {"task_id": "m1", "uses": []})
    assert _run(cu, tmp_path).returncode == 0
    _write(tmp_path, "poetry/source_usage.json", {"task_id": "m1", "uses": [
        {"use_id": "u1", "source_id": "ghost", "use": "query_only",
         "stage": "compose", "consumed_by": "draft"}]})
    assert _run(cu, tmp_path).returncode != 0

    mv = _cmd("manifest-validate")
    _write(tmp_path, "poetry/artifact_manifest.json", build_modern_manifest("m1"))
    assert _run(mv, tmp_path).returncode == 0
