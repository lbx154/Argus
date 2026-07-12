"""Tests for the Manager new-domain authoring flow in ``Manager.divide``.

``Manager.divide`` makes ONE grounded agent call (``decide_vertical``, see
``manager/domain_author.py``): the model either picks an existing built-in
vertical / project data domain (``{"choice": "existing", ...}``) or authors a
new data domain (``{"choice": "new", ...}``). A fake runner returns one of
these two JSON shapes so the flow is exercised without a real backend.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.manager import Manager
from argus_skill.manager.domain_author import VerticalDecisionError
from argus_skill.skills import stage_checklists as sc
from argus_skill.skills import vertical_select as vs


class _FakeResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"


class _FakeRunner:
    """Returns the same vertical-decision JSON for every call."""

    def __init__(self, decision: dict) -> None:
        self._decision = decision
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "options": options, "run_label": run_label})
        return _FakeResult(json.dumps(self._decision))


_NEW_DOMAIN_DECISION = {
    "choice": "new",
    "name": "robotics_sim",
    "stages": ["scope", "simulate", "measure", "report"],
    "rationale": "novel",
    "confidence": 0.8,
}
_EXISTING_RESEARCH_DECISION = {
    "choice": "existing",
    "vertical": "research",
    "rationale": "the task is a paper with a literature review and submission",
}
_NEW_MATH_DOMAIN_DECISION = {
    "choice": "new",
    "vertical": "math_conjecture",
    "stages": ["literature", "experiment", "proof", "review"],
    "rationale": "task-specific mathematical route",
    "confidence": 0.9,
}
# A task carrying NO preset (research/optimize/quant) signal → novel domain.
_NOVEL_TASK = "Build a closed-loop pick-and-place controller in a MuJoCo world"


def test_autonomous_authors_and_commits(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_NEW_DOMAIN_DECISION))
    div = mgr.divide(_NOVEL_TASK)
    assert div.kind == "custom" and div.vertical == "robotics_sim"
    assert div.pending_confirmation is False
    # Written + persisted so the supervisor trusts it.
    assert (tmp_path / "research" / "DOMAINS" / "robotics_sim.json").exists()
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == "scope"


def test_ask_mode_defers_write(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_NEW_DOMAIN_DECISION))
    div = mgr.divide(_NOVEL_TASK, ask_on_new_domain=True)
    assert div.pending_confirmation is True
    assert div.proposed_domain is not None
    # Nothing written yet.
    assert not (tmp_path / "research" / "DOMAINS").exists()
    # FAIL-SOFT: nothing persisted yet, so resolve_vertical falls back to the
    # safe default rather than hard-crashing (the Manager's committed domain wins
    # once persisted, below).
    assert vs.resolve_vertical(tmp_path) == "research"
    # Operator confirms.
    committed = mgr.commit_domain(div.task, div.proposed_domain)
    assert committed.vertical == "robotics_sim"
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


def test_preset_task_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_EXISTING_RESEARCH_DECISION))
    div = mgr.divide("write an EMNLP paper with a literature review and submission")
    assert div.vertical == "research" and div.kind == "research"
    assert not (tmp_path / "research" / "DOMAINS").exists()  # no domain authored


def test_explicit_math_env_reuses_builtin_without_authoring_data_domain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    runner = _FakeRunner(_NEW_MATH_DOMAIN_DECISION)

    div = Manager(project_root=tmp_path, runner=runner).divide(
        "Investigate this open conjecture using literature, computation, and proof attempts"
    )

    assert div.vertical == "math"
    assert runner.calls == []
    assert not (tmp_path / "research" / "DOMAINS" / "math_conjecture.json").exists()
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math"


def test_explicit_math_env_replaces_persisted_math_conjecture_selection(
    tmp_path, monkeypatch
):
    from argus_skill.verticals._data_domain import write_data_domain

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    write_data_domain(
        tmp_path,
        "math_conjecture",
        stages=["literature", "experiment", "proof", "review"],
    )
    vs.persist_vertical(tmp_path, "math_conjecture")
    assert vs.resolve_vertical(tmp_path) == "math_conjecture"

    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    runner = _FakeRunner(_NEW_MATH_DOMAIN_DECISION)
    div = Manager(project_root=tmp_path, runner=runner).divide(
        "Continue investigating the open conjecture"
    )

    assert div.vertical == "math"
    assert runner.calls == []
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math"
    assert vs.resolve_vertical(tmp_path) == "math"


def test_no_runner_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    # No backend → cannot decide → FAIL-HARD, no silent research fallback.
    with pytest.raises(VerticalDecisionError):
        Manager(project_root=tmp_path).divide(_NOVEL_TASK)


def test_authoring_call_is_grounded_not_a_blind_guess(tmp_path, monkeypatch):
    """Regression: the vertical decision must give the Manager real repo access
    (pinned working_dir + dangerous_yolo/full_auto matching the codebase's
    safe_mode convention) instead of a text-only classify call with no tools."""
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-vertical-decide")
    opts = call["options"]
    assert opts.working_dir == str(tmp_path)
    assert opts.dangerous_yolo is True
    assert opts.full_auto is False
    assert "shell access" in call["prompt"].lower()
    assert "investigate" in call["prompt"].lower()


def test_authoring_call_respects_safe_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-vertical-decide")
    opts = call["options"]
    assert opts.dangerous_yolo is False
    assert opts.full_auto is True
