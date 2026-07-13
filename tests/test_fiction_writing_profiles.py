"""Loop 10: fiction genre PROFILES really change the plan/rubric — and never
bypass a contract.

A profile is not a new vertical and not a dead data file: its knobs are distinct
per profile, it is carried into the brief, it changed the review-stage reviewer
guidance, and an UNKNOWN profile fails the intake gate at run time. Meanwhile the
Task/Review/Artifact/Provenance gates are untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.verticals.fiction_writing.intake import brief_from_envelope
from argus_skill.verticals.fiction_writing.profiles import (
    DEFAULT_PROFILE,
    FICTION_PROFILES,
    FictionProfileError,
    resolve_profile,
)
from argus_skill.verticals.fiction_writing.stages import (
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
)

_REPO = Path(__file__).resolve().parents[1]


def test_resolve_known_default_unknown():
    assert resolve_profile("web_fiction")["name"] == "web_fiction"
    assert resolve_profile(None)["name"] == DEFAULT_PROFILE
    assert resolve_profile("")["name"] == DEFAULT_PROFILE
    with pytest.raises(FictionProfileError, match="unknown fiction profile"):
        resolve_profile("cyberpunk_haiku")


def test_profiles_are_distinct_not_a_dead_file():
    web = resolve_profile("web_fiction")
    lit = resolve_profile("literary_fiction")
    assert web["pacing"] != lit["pacing"]
    assert web["exposition_tolerance"] == "high"
    assert lit["exposition_tolerance"] == "low"
    assert web["chapter_hooks"] == "required"
    assert lit["character_complexity"] == "high"
    assert web["reviewer_emphasis"] != lit["reviewer_emphasis"]
    assert len(FICTION_PROFILES) == 5


def _env(**kw):
    base = {"task_id": "f1", "mode": "from_scratch", "language": "zh",
            "form": "chapter", "intent": "写一章"}
    base.update(kw)
    return base


def test_brief_carries_profile_default_and_named():
    assert brief_from_envelope(_env())["profile"]["name"] == DEFAULT_PROFILE
    b = brief_from_envelope(_env(output_requirements={"profile": "literary_fiction"}))
    assert b["profile"]["name"] == "literary_fiction"
    assert b["profile"]["character_complexity"] == "high"


def test_unknown_profile_rejected_at_intake():
    with pytest.raises(Exception):
        brief_from_envelope(_env(output_requirements={"profile": "bogus"}))


def test_profile_does_not_bypass_contracts():
    # a non-narrative form is still rejected even under a valid profile
    with pytest.raises(Exception):
        brief_from_envelope(_env(form="quatrain",
                                 output_requirements={"profile": "web_fiction"}))
    # the review/revise stages still gate on the shared contracts
    review = " ".join(c for _d, c in STAGE_CHECKS["review"])
    revise = " ".join(c for _d, c in STAGE_CHECKS["revise"])
    assert "review_check" in review
    assert "manifest_check" in revise and "check-plan" in revise


def test_review_rubric_now_references_profile():
    _skill, instr, files = REVIEWER_CHECKLISTS["review"]
    assert "PROFILE" in instr or "profile" in instr
    assert "fiction/creative_brief.json" in files


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                          text=True, env=env)


def _intake_cmd() -> str:
    for _d, c in STAGE_CHECKS["intake"]:
        if "intake_check validate" in c:
            return c
    raise AssertionError("intake_check validate not wired")


def test_runtime_intake_gate_rejects_unknown_profile(tmp_path):
    cmd = _intake_cmd()
    (tmp_path / "fiction").mkdir()

    def w(env):
        (tmp_path / "fiction" / "task_envelope.json").write_text(
            json.dumps(env, ensure_ascii=False), encoding="utf-8")

    w(_env(output_requirements={"profile": "web_fiction"}))
    assert _run(cmd, tmp_path).returncode == 0
    w(_env(output_requirements={"profile": "nonsense"}))
    assert _run(cmd, tmp_path).returncode != 0
