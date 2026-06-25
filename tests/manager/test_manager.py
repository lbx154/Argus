"""Tests for the Manager division layer — triage / stage split / commit.

Pure-function tests (no LLM): the classifier runs in heuristic mode (runner=None).
"""
from __future__ import annotations

import json

from argus_skill.manager import Manager, Division
from argus_skill.verticals.research.stages import STAGE_ORDER as RESEARCH_STAGES


def test_triage_research_paper():
    vertical, kind, regular = Manager().triage(
        "write a paper on retrieval for EMNLP and prepare the submission"
    )
    assert vertical == "research"
    assert kind == "research"
    assert regular is True


def test_triage_optimize_routes_to_nanochat():
    vertical, kind, regular = Manager().triage(
        "minimize val_bpb on the nanochat train.py"
    )
    assert vertical == "nanochat"          # _route_optimize_vertical picked the bpb vertical
    assert kind == "optimize"
    assert regular is True


def test_triage_freeform_is_not_regular():
    _, _, regular = Manager().triage("hi there")
    assert regular is False                # no research/optimize signal → free-form


def test_plan_stages_research_is_the_8_stage_pipeline():
    stages = Manager().plan_stages("research")
    assert stages == list(RESEARCH_STAGES)
    assert stages[0] == "research" and stages[-1] == "submission"
    assert len(stages) == 8


def test_divide_commits_vertical_so_supervisor_trusts_it(tmp_path):
    mgr = Manager(project_root=tmp_path)
    d = mgr.divide("minimize val_bpb on nanochat train.py")
    assert isinstance(d, Division)
    assert d.vertical == "nanochat" and d.kind == "optimize"
    # persisted into PIPELINE_STATE.json — the supervisor reads & trusts this
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "nanochat"


def test_divide_research_persists_and_lists_8_stages(tmp_path):
    d = Manager(project_root=tmp_path).divide("draft a paper for EMNLP submission")
    assert d.vertical == "research"
    assert d.stages == list(RESEARCH_STAGES)
    assert "regular" in d.headline()
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"


class _FakeResult:
    """Minimal RunnerResult shape the router classifier reads."""
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


def test_manager_no_runner_treats_free_text_as_task():
    # No backend → can't chat-classify → safe default is TASK (never drop work).
    assert Manager(runner=None).is_conversational("hi") is False


def test_manager_owns_chat_vs_task_decision():
    # The Manager is the decision-maker: a clear CHAT verdict → True, else TASK.
    mgr = Manager()
    assert mgr.is_conversational(
        "hello there", run_exec=lambda p: _FakeResult("CHAT")
    ) is True
    assert mgr.is_conversational(
        "minimize val_bpb on train.py", run_exec=lambda p: _FakeResult("TASK")
    ) is False
