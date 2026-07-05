"""Tests for the Manager division layer — triage / stage split / commit.

Pure-function tests (no LLM): the classifier runs in heuristic mode (runner=None).
"""
from __future__ import annotations

import json

from argus_skill.manager import Division, Manager
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


def test_manager_owns_self_vs_team_decision():
    mgr = Manager()
    assert mgr.is_conversational(
        "hello there", run_exec=lambda p: _FakeResult("SELF")
    ) is True
    assert mgr.is_conversational(
        "minimize val_bpb on train.py", run_exec=lambda p: _FakeResult("TEAM")
    ) is False


# ---- F6: pure classification must NOT fire the skill matcher ----------------

class _CountingMission:
    """Stand-in ManagerMission that counts matcher calls (the LLM burn F6 cuts)."""
    def __init__(self) -> None:
        self.calls = 0

    def match(self, objective: str):
        self.calls += 1

        class _M:
            block = ""
        return _M()


def _mgr_with_store(tmp_path):
    mgr = Manager(project_root=tmp_path, runner=None, skill_store=object())
    mgr.mission = _CountingMission()  # type: ignore[assignment]
    return mgr


def test_role_skill_block_match_false_still_injects_fixed_role(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    block = mgr._role_skill_block("optimize a CUDA kernel", match=False)
    assert block.strip()                       # fixed role identity still injected
    assert "manager" in block.lower()
    assert mgr.mission.calls == 0              # matcher NEVER called


def test_role_skill_block_match_true_fires_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    mgr._role_skill_block("optimize a CUDA kernel", match=True)
    assert mgr.mission.calls == 1             # default path still matches


def test_route_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.route("hello", run_exec=lambda p: _FakeResult("TEAM"))
    assert mgr.mission.calls == 0
    assert out in ("simple", "complex")


def test_is_conversational_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.is_conversational("hi there", run_exec=lambda p: _FakeResult("SELF"))
    assert mgr.mission.calls == 0
    assert out is True
