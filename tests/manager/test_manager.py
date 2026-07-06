"""Tests for the Manager division layer — decide_vertical / stage split / commit.

The Manager decides the vertical via ONE grounded agent call; these tests use a
fake runner returning the decision JSON (no real LLM).
"""
from __future__ import annotations

import json

from argus_skill.manager import Division, Manager
from argus_skill.verticals.research.stages import STAGE_ORDER as RESEARCH_STAGES


class _DecisionResult:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"


class _DecisionRunner:
    """Fake runner: returns a fixed vertical-decision JSON for every call."""

    def __init__(self, decision: dict) -> None:
        self._decision = decision

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        return _DecisionResult(json.dumps(self._decision))


def _existing(vertical: str) -> _DecisionRunner:
    return _DecisionRunner({"choice": "existing", "vertical": vertical})


def test_triage_existing_research():
    vertical, kind, regular = Manager(runner=_existing("research")).triage(
        "write a paper on retrieval for EMNLP and prepare the submission"
    )
    assert vertical == "research"
    assert kind == "research"
    assert regular is True


def test_triage_existing_nanochat_is_optimize():
    vertical, kind, regular = Manager(runner=_existing("nanochat")).triage(
        "minimize val_bpb on the nanochat train.py"
    )
    assert vertical == "nanochat"
    assert kind == "optimize"
    assert regular is True


def test_plan_stages_research_is_the_8_stage_pipeline():
    stages = Manager().plan_stages("research")
    assert stages == list(RESEARCH_STAGES)
    assert stages[0] == "research" and stages[-1] == "submission"
    assert len(stages) == 8


def test_divide_commits_vertical_so_supervisor_trusts_it(tmp_path):
    mgr = Manager(project_root=tmp_path, runner=_existing("nanochat"))
    d = mgr.divide("minimize val_bpb on nanochat train.py")
    assert isinstance(d, Division)
    assert d.vertical == "nanochat" and d.kind == "optimize"
    # persisted into PIPELINE_STATE.json — the supervisor reads & trusts this
    state = json.loads((tmp_path / "research" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "nanochat"


def test_divide_research_persists_and_lists_8_stages(tmp_path):
    d = Manager(project_root=tmp_path, runner=_existing("research")).divide(
        "draft a paper for EMNLP submission"
    )
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


# ---- needs_persistence: BOUNDED vs STANDING (auto continuous-mode arming) ---

def test_manager_no_runner_treats_free_text_as_bounded():
    # No backend → can't classify → safe default is BOUNDED (never silently
    # force an expensive 7x24 campaign onto a task that did not ask for one).
    assert Manager(runner=None).needs_persistence("optimize everything forever") is False


def test_manager_owns_bounded_vs_standing_decision():
    mgr = Manager()
    assert mgr.needs_persistence(
        "optimize as many kernels as possible", run_exec=lambda p: _FakeResult("STANDING")
    ) is True
    assert mgr.needs_persistence(
        "fix the flaky test in test_foo.py", run_exec=lambda p: _FakeResult("BOUNDED")
    ) is False


def test_needs_persistence_does_not_fire_matcher(tmp_path):
    mgr = _mgr_with_store(tmp_path)
    out = mgr.needs_persistence(
        "keep improving this indefinitely", run_exec=lambda p: _FakeResult("STANDING")
    )
    assert mgr.mission.calls == 0
    assert out is True
