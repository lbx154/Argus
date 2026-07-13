"""Runtime closed-loop test: the fiction review/revise STAGE_CHECKS actually run
the shared review contract at run time (subprocess, exactly as the stage-check
runner in argus_skill.tools.stage_check does).

This is the proof the contract is CONSUMED by the runtime stage gates, not only
by unit tests calling the adapter directly. If the wiring in stages.py is
removed, the `_checks_with` lookups return [] and these tests fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.verticals.fiction_writing.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]

_BLOCKING = {
    "id": "f1", "type": "knowledge", "severity": "critical", "blocking": True,
    "location": "第3段", "evidence": "越权知情",
    "suggested_action": "改为推测语气", "must_not_break": ["c_b 的知情边界"],
}


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


def test_review_stage_wires_the_contract_check():
    assert _checks_with("review", "review_check"), \
        "review stage must gate on the review contract at run time"


def test_revise_stage_wires_plan_coverage_check():
    assert _checks_with("revise", "check-plan"), \
        "revise stage must gate on revision-plan coverage at run time"


def test_runtime_review_gate_passes_valid_fails_malformed(tmp_path):
    cmd = _checks_with("review", "review_check")[0]
    _write(tmp_path / "fiction" / "review.json",
           {"verdict": "revise", "findings": [_BLOCKING]})
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    # a malformed review must FAIL the gate (never silently pass)
    (tmp_path / "fiction" / "review.json").write_text("{not json", encoding="utf-8")
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0


def test_runtime_revise_gate_requires_plan_coverage(tmp_path):
    cmd = _checks_with("revise", "check-plan")[0]
    _write(tmp_path / "fiction" / "review.json",
           {"verdict": "revise", "findings": [_BLOCKING]})
    good = [{"finding_id": "f1", "type": "knowledge", "severity": "critical",
             "blocking": True, "location": "第3段", "suggested_action": "改为推测语气",
             "must_not_break": ["c_b 的知情边界"]}]
    _write(tmp_path / "fiction" / "revision_plan.json", good)
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    # a plan that drops the blocking finding must FAIL the gate
    _write(tmp_path / "fiction" / "revision_plan.json", [])
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0
