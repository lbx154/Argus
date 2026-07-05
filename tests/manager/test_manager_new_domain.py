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
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "options": options, "run_label": run_label})
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


def test_authoring_call_is_grounded_not_a_blind_guess(tmp_path, monkeypatch):
    """Regression: domain authoring must give the Manager real repo access
    (pinned working_dir + dangerous_yolo/full_auto matching the codebase's
    safe_mode convention) instead of a text-only classify call with no tools."""
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)
    runner = _FakeRunner(_PROPOSAL)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-domain-author")
    opts = call["options"]
    assert opts.working_dir == str(tmp_path)
    assert opts.dangerous_yolo is True
    assert opts.full_auto is False
    assert "shell access" in call["prompt"].lower()
    assert "investigate" in call["prompt"].lower()


def test_authoring_call_respects_safe_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    runner = _FakeRunner(_PROPOSAL)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-domain-author")
    opts = call["options"]
    assert opts.dangerous_yolo is False
    assert opts.full_auto is True
