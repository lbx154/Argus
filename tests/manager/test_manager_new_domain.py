"""Tests for the Manager new-domain authoring flow in ``Manager.divide``.

The Manager's tool-free classification pass must escalate potential new domains to one bounded
grounded call, which may then author the domain. Fake runners exercise both
requests without a real backend.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.manager import Manager
from argus_skill.manager.domain_author import VerticalDecisionError
from argus_skill.skills import stage_machine as sc
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
    "workflow_mode": "staged",
    "execution_task": "Build and evaluate the MuJoCo controller.",
}
_EXISTING_RESEARCH_DECISION = {
    "choice": "existing",
    "vertical": "research",
    "confidence": 0.95,
    "workflow_mode": "staged",
    "rationale": "the task is a paper with a literature review and submission",
    "execution_task": "Write the paper and prepare it for submission.",
    "research_target_level": "publishable",
}
_NEW_MATH_DOMAIN_DECISION = {
    "choice": "new",
    "vertical": "math_conjecture",
    "stages": ["literature", "experiment", "proof", "review"],
    "rationale": "task-specific mathematical route",
    "confidence": 0.9,
    "workflow_mode": "staged",
    "research_target_level": "doctoral",
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
    assert committed.execution_task == "Build and evaluate the MuJoCo controller."
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


def test_preset_task_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    mgr = Manager(project_root=tmp_path, runner=_FakeRunner(_EXISTING_RESEARCH_DECISION))
    div = mgr.divide("write an EMNLP paper with a literature review and submission")
    assert div.vertical == "research" and div.kind == "research"
    assert not (tmp_path / "research" / "DOMAINS").exists()  # no domain authored


def test_vertical_env_cannot_replace_manager_authored_domain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    runner = _FakeRunner(_NEW_MATH_DOMAIN_DECISION)

    div = Manager(project_root=tmp_path, runner=runner).divide(
        "Investigate this open conjecture using literature, computation, and proof attempts"
    )

    assert div.vertical == "math_conjecture"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    assert (tmp_path / "research" / "DOMAINS" / "math_conjecture.json").exists()
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math_conjecture"


def test_vertical_env_does_not_override_manager_reclassification(
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

    assert div.vertical == "math_conjecture_2"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math_conjecture_2"
    assert vs.resolve_vertical(tmp_path) == "math_conjecture_2"


def test_no_runner_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    # No backend → cannot decide → FAIL-HARD, no silent research fallback.
    with pytest.raises(VerticalDecisionError):
        Manager(project_root=tmp_path).divide(_NOVEL_TASK)


def test_backend_failure_preserves_actionable_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)

    class _FailedRunner:
        def run_exec(self, **kwargs):
            result = _FakeResult("")
            result.exit_code = -1
            result.fatal_error = (
                "refused before start: unresolved provider cost blocks new calls"
            )
            return result

    with pytest.raises(
        VerticalDecisionError,
        match="unresolved provider cost blocks new calls",
    ):
        Manager(project_root=tmp_path, runner=_FailedRunner()).divide(_NOVEL_TASK)


def test_zero_exit_fatal_error_preserves_actionable_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)

    class _FailedRunner:
        def run_exec(self, **kwargs):
            result = _FakeResult("")
            result.exit_code = 0
            result.fatal_error = "provider policy denied this turn"
            return result

    with pytest.raises(VerticalDecisionError, match="provider policy denied"):
        Manager(project_root=tmp_path, runner=_FailedRunner()).divide(_NOVEL_TASK)


def test_authoring_call_is_grounded_not_a_blind_guess(tmp_path, monkeypatch):
    """Regression: the vertical decision must give the Manager real repo access
    (pinned working_dir + dangerous_yolo/full_auto matching the codebase's
    safe_mode convention) instead of a text-only classify call with no tools."""
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_SAFE_MODE", raising=False)
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast",
        "manager-classify-grounded",
    ]
    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-grounded")
    opts = call["options"]
    assert opts.working_dir == str(tmp_path)
    assert opts.sandbox_mode is None
    assert opts.dangerous_yolo is True
    assert opts.full_auto is False
    assert opts.reasoning_effort == "low"
    assert "full repository tool environment" in call["prompt"].lower()
    assert "investigate" in call["prompt"].lower()


def test_copilot_vertical_decision_does_not_auto_inject_repo_instructions(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    runner = _FakeRunner(_EXISTING_RESEARCH_DECISION)
    runner._backend_name = "copilot"

    Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "write a research paper",
    )

    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-fast")
    assert call["options"].extra_args == [
        "--no-custom-instructions",
        "--disable-builtin-mcps",
        "--available-tools=",
        "--context",
        "default",
    ]
    assert call["options"].sandbox_mode is None
    assert "NO tools" in call["prompt"]
    assert "shell access" not in call["prompt"].lower()


def test_authoring_call_respects_safe_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    runner = _FakeRunner(_NEW_DOMAIN_DECISION)
    mgr = Manager(project_root=tmp_path, runner=runner)
    mgr.divide(_NOVEL_TASK)

    call = next(c for c in runner.calls if c["run_label"] == "manager-classify-grounded")
    opts = call["options"]
    assert opts.sandbox_mode is None
    assert opts.dangerous_yolo is True
    assert opts.full_auto is False
