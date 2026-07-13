"""Migration capstone: fiction consumes ALL FOUR shared literary contracts at run
time, uniformly — and the Task Envelope gate (the last to get a runtime check)
actually bites.

The first test is the regression guard for the whole "fiction migrated to the
shared protocols" claim: if anyone removes a contract's wiring from stages.py,
this fails. The rest run the intake envelope gate as a subprocess (exactly as the
stage-check runner does) to prove a malformed or mis-routed task envelope fails
the intake stage, not just a unit test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.verticals.fiction_writing.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _cmds(stage: str) -> list[str]:
    return [cmd for _desc, cmd in STAGE_CHECKS[stage]]


def _has(stage: str, needle: str) -> bool:
    return any(needle in cmd for cmd in _cmds(stage))


def test_all_four_shared_contracts_are_runtime_wired():
    # Task Envelope (loop 1) — the gate added by loop 5
    assert _has("intake", "intake_check validate"), "Task Envelope not gated at intake"
    # Review Contract (loop 2)
    assert _has("review", "review_check validate"), "Review contract not gated at review"
    assert _has("revise", "check-plan"), "Revision-plan coverage not gated at revise"
    # Artifact Manifest (loop 3)
    assert _has("revise", "manifest_check"), "Artifact manifest not gated at revise"
    # Provenance / Source registry (loop 4)
    assert _has("intake", "validate-registry"), "Source registry not gated at intake"
    assert _has("review", "check-usage"), "Source-usage ledger not gated at review"


# --------------------------------------------------------------------------- #
# runtime: the intake envelope gate passes valid and fails malformed / misrouted
# --------------------------------------------------------------------------- #

def _envelope_cmd() -> str:
    return [cmd for _d, cmd in STAGE_CHECKS["intake"] if "intake_check validate" in cmd][0]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, env=env)


def _write_envelope(base: Path, obj) -> None:
    p = base / "fiction" / "task_envelope.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


_VALID = {"task_id": "t1", "mode": "from_scratch", "language": "zh",
          "form": "short_story", "intent": "写一个都市悬疑短篇"}


def test_runtime_envelope_gate_passes_valid(tmp_path):
    _write_envelope(tmp_path, _VALID)
    ok = _run(_envelope_cmd(), tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_runtime_envelope_gate_fails_when_missing(tmp_path):
    missing = _run(_envelope_cmd(), tmp_path)
    assert missing.returncode != 0


def test_runtime_envelope_gate_fails_malformed(tmp_path):
    # unknown mode -> shared contract rejects it
    _write_envelope(tmp_path, {**_VALID, "mode": "teleport"})
    bad = _run(_envelope_cmd(), tmp_path)
    assert bad.returncode != 0


def test_runtime_envelope_gate_rejects_non_fiction_form(tmp_path):
    # a poetry quatrain routed to fiction fails loudly at intake
    _write_envelope(tmp_path, {**_VALID, "form": "quatrain", "intent": "写一首七言绝句"})
    bad = _run(_envelope_cmd(), tmp_path)
    assert bad.returncode != 0
