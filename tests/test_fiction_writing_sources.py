"""Closed-loop tests: fiction's committed source registry is valid AND the
intake/review STAGE_CHECKS enforce provenance at run time.

The runtime half runs the actual STAGE_CHECKS commands as subprocesses (exactly as
argus_skill.tools.stage_check does): the registry gate at intake, the mandatory
source-usage ledger gate at review. It proves the ledger is NOT bypassable —
a missing ledger fails, and the review check carries no `test ! -f ... ||`
conditional escape hatch. If the wiring in stages.py is removed or weakened back
to conditional, these tests fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.verticals.fiction_writing.sources import load_fiction_registry
from argus_skill.verticals.fiction_writing.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# the committed registry is valid, and the wiring is present + UNCONDITIONAL
# --------------------------------------------------------------------------- #

def test_committed_fiction_registry_is_valid():
    reg = load_fiction_registry()  # loads + validates the real sources.yaml
    assert reg.get("items"), "registry should list at least one source item"


def _checks_with(stage: str, needle: str) -> list[str]:
    return [cmd for _desc, cmd in STAGE_CHECKS[stage] if needle in cmd]


def test_intake_wires_registry_validation():
    assert _checks_with("intake", "validate-registry"), \
        "intake must validate the source registry at run time"


def test_review_wires_mandatory_ledger_check():
    # ledger presence + contract validation are both wired
    assert _checks_with("review", "test -s fiction/source_usage.json"), \
        "review must require the source-usage ledger to exist"
    assert _checks_with("review", "check-usage"), \
        "review must validate the source-usage ledger against the contract"


def test_review_ledger_check_is_unconditional():
    # anti-regression: nobody may reintroduce a `test ! -f ... ||` bypass that
    # lets a mission skip provenance by simply not writing the ledger.
    for cmd in _checks_with("review", "check-usage"):
        assert "! -f" not in cmd and "||" not in cmd, \
            f"source-usage check must be unconditional, got: {cmd!r}"


# --------------------------------------------------------------------------- #
# runtime: the STAGE_CHECKS commands actually enforce provenance
# --------------------------------------------------------------------------- #

def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, env=env)


def _write_ledger(base: Path, uses) -> None:
    p = base / "fiction" / "source_usage.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"task_id": "t1", "uses": uses}, ensure_ascii=False),
                 encoding="utf-8")


def _use(uid, source_id, use, **kw):
    u = {"use_id": uid, "source_id": source_id, "use": use,
         "stage": "draft", "consumed_by": "draft"}
    u.update(kw)
    return u


def test_runtime_registry_gate_passes(tmp_path):
    cmd = _checks_with("intake", "validate-registry")[0]
    ok = _run(cmd, tmp_path)  # registry path is absolute; cwd irrelevant
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_runtime_ledger_missing_fails(tmp_path):
    # unconditional: no ledger on disk -> the check FAILS (not skipped)
    cmd = _checks_with("review", "check-usage")[0]
    missing = _run(cmd, tmp_path)
    assert missing.returncode != 0


def test_runtime_empty_ledger_passes(tmp_path):
    cmd = _checks_with("review", "check-usage")[0]
    _write_ledger(tmp_path, [])
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_runtime_valid_usage_passes(tmp_path):
    cmd = _checks_with("review", "check-usage")[0]
    # bcc_query_access allows query_only in the committed registry
    _write_ledger(tmp_path, [_use("u1", "bcc_query_access", "query_only")])
    ok = _run(cmd, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_runtime_unregistered_source_fails(tmp_path):
    cmd = _checks_with("review", "check-usage")[0]
    _write_ledger(tmp_path, [_use("u1", "does_not_exist", "query_only")])
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0


def test_runtime_prohibited_use_fails(tmp_path):
    cmd = _checks_with("review", "check-usage")[0]
    # bcc_query_access prohibits model_training
    _write_ledger(tmp_path, [_use("u1", "bcc_query_access", "model_training")])
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0


def test_runtime_queried_source_cannot_be_cited(tmp_path):
    cmd = _checks_with("review", "check-usage")[0]
    # gutenberg_1661_sherlock allows evidence_citation but is NOT ingested ->
    # the honesty guard rejects citing a source we never took in
    _write_ledger(tmp_path, [_use("u1", "gutenberg_1661_sherlock",
                                  "evidence_citation", citation="Doyle, 1892")])
    bad = _run(cmd, tmp_path)
    assert bad.returncode != 0
