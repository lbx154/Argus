"""Tests for the Manager new-domain authoring flow in ``Manager.divide``.

A fake runner returns a domain proposal so the LLM authoring path is exercised
without a real backend.
"""
from __future__ import annotations

import json

from argus_skill.manager import Manager
from argus_skill.skills import stage_checklists as sc
from argus_skill.skills import vertical_select as vs


class _FakeResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"


class _FakeRunner:
    """Returns the same domain proposal for every call."""

    def __init__(self, proposal: dict) -> None:
        self._proposal = proposal

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        return _FakeResult(json.dumps(self._proposal))


_PROPOSAL = {
    "name": "robotics_sim",
    "stages": ["scope", "simulate", "measure", "report"],
    "rationale": "novel",
    "confidence": 0.8,
}
# A task carrying NO preset (research/optimize/quant) signal → novel domain.
_NOVEL_TASK = "Build a closed-loop pick-and-place controller in a MuJoCo world"


def test_autonomous_authors_and_commits(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_PROPOSAL))
    div = mgr.divide(_NOVEL_TASK)
    assert div.kind == "custom" and div.vertical == "robotics_sim"
    assert div.pending_confirmation is False
    # Written + persisted so the supervisor trusts it.
    assert (tmp_path / "research" / "DOMAINS" / "robotics_sim.json").exists()
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == "scope"


def test_ask_mode_defers_write(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_PROPOSAL))
    div = mgr.divide(_NOVEL_TASK, ask_on_new_domain=True)
    assert div.pending_confirmation is True
    assert div.proposed_domain is not None
    # Nothing written yet.
    assert not (tmp_path / "research" / "DOMAINS").exists()
    assert vs.resolve_vertical(tmp_path) == "research"      # still the default
    # Operator confirms.
    committed = mgr.commit_domain(div.task, div.proposed_domain)
    assert committed.vertical == "robotics_sim"
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


def test_preset_task_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_PROPOSAL))
    div = mgr.divide("write an EMNLP paper with a literature review and submission")
    assert div.vertical == "research" and div.kind == "research"
    assert not (tmp_path / "research" / "DOMAINS").exists()  # no domain authored


def test_no_runner_falls_back_to_research(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    # No backend → cannot author → safe research default (never worse than before).
    div = Manager(project_root=tmp_path).divide(_NOVEL_TASK)
    assert div.vertical == "research"
