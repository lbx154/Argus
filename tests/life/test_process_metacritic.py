"""Tests for the read-only process meta-critic (self-distillation step 2)."""
from __future__ import annotations

import json

from argus_skill.core.models import RunnerOptions
from argus_skill.life.process_metacritic import (
    build_metacritic_prompt,
    distill_process_lessons,
    parse_lessons,
    persist_lessons,
)


class _FakeResult:
    def __init__(self, text: str):
        self._t = text
        self.agent_messages = [text]
        self.exit_code = 0
        self.output_tokens = 10

    def message(self) -> str:
        return self._t


class _FakeBackend:
    """Minimal deterministic RunnerBackend for tests."""

    def __init__(self, text: str):
        self.text = text
        self.last_prompt: str | None = None

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.last_prompt = prompt
        return _FakeResult(self.text)


def test_parse_lessons_tolerant_of_fences_and_prose():
    text = (
        "Sure, here is my audit:\n```json\n"
        '[{"dominant_pattern": "P", "incentive_contradiction": "C",'
        ' "evidence": "E", "proposed_process_fix": "F"}]\n```\nhope that helps'
    )
    lessons = parse_lessons(text, n_missions=42)
    assert len(lessons) == 1
    le = lessons[0]
    assert le.dominant_pattern == "P"
    assert le.status == "shadow"        # never auto-applied
    assert le.n_missions == 42
    assert le.id and len(le.id) == 12   # stable content id


def test_parse_empty_or_garbage():
    assert parse_lessons("no json at all", n_missions=1) == []
    assert parse_lessons("", n_missions=1) == []
    assert parse_lessons("[not valid json}", n_missions=1) == []


def test_distill_includes_ledger_and_surface_in_prompt():
    canned = json.dumps([{
        "dominant_pattern": "env walls re-hit",
        "incentive_contradiction": "lesson trigger gated on skill_gap while dominant cause is environmental",
        "evidence": "environmental=477 vs n_mission_lessons=2",
        "proposed_process_fix": "broaden lesson extraction to environmental/execution causes",
    }])
    be = _FakeBackend(canned)
    ledger = {"n_missions": 852, "failure_cause_hist": {"environmental": 477}}
    lessons = distill_process_lessons(
        be, ledger,
        incentive_excerpts={"reviewer": "failure_cause==skill_gap gates mission_lesson"},
        options=RunnerOptions(),
    )
    assert len(lessons) == 1
    assert lessons[0].n_missions == 852
    # the corpus numbers AND the source surface were actually put in front of the critic
    assert "CORPUS PROCESS LEDGER" in be.last_prompt
    assert "477" in be.last_prompt
    assert "failure_cause==skill_gap" in be.last_prompt
    # outcome-immutability invariant is stated in the instruction
    assert "FROZEN" in be.last_prompt


def test_persist_lessons_roundtrip(tmp_path):
    be = _FakeBackend(json.dumps([{
        "dominant_pattern": "x", "incentive_contradiction": "y",
        "evidence": "z", "proposed_process_fix": "w",
    }]))
    lessons = distill_process_lessons(be, {"n_missions": 5})
    fp = persist_lessons(lessons, tmp_path, "epoch_test")
    assert fp.exists()
    data = json.loads(fp.read_text())
    assert data[0]["status"] == "shadow"
    assert data[0]["proposed_process_fix"] == "w"


def test_empty_output_yields_no_lessons():
    assert distill_process_lessons(_FakeBackend("I refuse"), {"n_missions": 1}) == []
