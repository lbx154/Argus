"""Manager wiring for role-owned, on-demand Skill discovery."""

from __future__ import annotations

from pathlib import Path

import argus.builtin_skills as _builtin
from argus.core.models import RunnerResult
from argus.manager import Manager
from argus.skills.store import (
    _ROLE_SUBDIRS,
    ROLE_CROSS_READ_POOLS,
    ROLE_SKILL_POOLS,
    role_of_path,
)
from tests.hand_off import stage

_BUILTIN_ROOT = Path(_builtin.__file__).parent


# --------------------------------------------------------------------------
# 1. planner role skill relocation (the bug)
# --------------------------------------------------------------------------
def test_planner_role_skill_moved_to_planner_dir() -> None:
    assert (_BUILTIN_ROOT / "planner" / "dependency-aware-task-decomposition.md").is_file()
    assert not (_BUILTIN_ROOT / "engineer" / "argus-planner-role.md").exists()
    assert not (_BUILTIN_ROOT / "planner" / "argus-planner-role.md").exists()






def test_planner_role_skill_no_longer_classified_as_engineer() -> None:
    p = _BUILTIN_ROOT / "planner" / "dependency-aware-task-decomposition.md"
    assert role_of_path(str(p), _BUILTIN_ROOT) == "planner"


# --------------------------------------------------------------------------
# 2. manager is a first-class role bucket
# --------------------------------------------------------------------------
def test_manager_in_role_subdirs_and_pools() -> None:
    assert "manager" in _ROLE_SUBDIRS
    assert ROLE_SKILL_POOLS["manager"] == frozenset({"manager", "general"})
    # Manager sees every other role's standards as read-only references.
    assert ROLE_CROSS_READ_POOLS["manager"] == frozenset({
        "engineer",
        "reviewer",
        "planner",
        "self",
    })




# --------------------------------------------------------------------------
# 3. Manager receives paths without preloading a Skill body
# --------------------------------------------------------------------------
class _StubReview:
    status = "continue"
    reason = "checklist not yet satisfied"
    checklist: list = []


class _CapturingStageRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, **kwargs) -> RunnerResult:
        self.calls.append(kwargs)
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                '{"action": "hold", "target_stage": "research", "reason": "stub"}'
            ],
        )


def test_manager_accepts_skill_store_and_is_backward_compatible() -> None:
    # No skill_store (default) — the existing signature/behaviour is preserved.
    m_default = Manager(project_root=".", runner=None)
    assert m_default.skill_store is None
    assert m_default.mission.role == "manager"
    # Explicit skill_store=None is also fine.
    m_none = Manager(project_root=".", runner=None, skill_store=None)
    assert m_none.skill_store is None


def test_manager_decision_prompt_carries_paths_not_skill_body(
    tmp_path: Path,
) -> None:
    from argus.skills.store import Skill, SkillStore

    store = SkillStore(tmp_path / "skills")
    store.save(
        Skill(
            "Private Manager procedure",
            "A reusable Manager procedure.",
            "# Private\n\nDO NOT PRELOAD THIS MANAGER BODY",
            path="manager/private-procedure.md",
        )
    )
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=store)

    cap = stage(move="hold")
    decision = mgr.decide_stage_transition(
        review=_StubReview(), project_root=tmp_path, run_exec=cap
    )

    assert cap.prompts, "manager never built a stage-decision prompt"
    prompt = cap.prompts[0]
    assert str(store.skills_dir.resolve()) in prompt
    assert "Role: manager" in prompt
    assert "DO NOT PRELOAD THIS MANAGER BODY" not in prompt
    assert "The Manager's role" not in prompt
    assert "ARGUS_ROLE_DECISION=" not in prompt
    assert "move=hold" in prompt
    assert "Right-sidebar presentation" not in prompt
    assert "LIVE_VIEW_PATHS" not in prompt
    assert decision.action == "hold"


def test_manager_decision_prompt_unchanged_without_store(tmp_path: Path) -> None:
    # With NO skill_store the decision prompt must NOT carry the role-skill header
    # (byte-for-byte back-compat with the pre-skill Manager).
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=None)
    cap = stage(move="hold")
    mgr.decide_stage_transition(review=_StubReview(), project_root=tmp_path, run_exec=cap)
    assert cap.prompts
    assert "Argus manager role skill" not in cap.prompts[0]


def test_manager_stage_decision_is_read_only(tmp_path: Path) -> None:
    runner = _CapturingStageRunner()

    decision = Manager(project_root=tmp_path, runner=runner).decide_stage_transition(
        review=_StubReview(),
        project_root=tmp_path,
    )

    assert decision.action == "hold"
    call = runner.calls[0]
    assert call["run_label"] == "manager-stage"
    assert call["options"].sandbox_mode == "read-only"
    assert call["options"].dangerous_yolo is False


def test_manager_stage_decision_reads_the_libraries_it_names_and_only_judges(
    tmp_path: Path,
) -> None:
    """s-757b9dac: the stage decision was pointed at the Skill libraries with no
    access to them and asked to edit Skills in a read-only turn. Three lookups
    failed, and its reply told the operator the session could not modify files."""
    from argus.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    runner = _CapturingStageRunner()

    Manager(project_root=tmp_path, runner=runner, skill_store=store).decide_stage_transition(
        review=_StubReview(),
        project_root=tmp_path,
    )

    (call,) = runner.calls
    library = str(store.skills_dir.resolve())
    assert library in call["prompt"]
    assert library in call["options"].add_dirs
    assert "Manager self-evolution" not in call["prompt"]
    assert "you change no files" in call["prompt"]


def test_manager_reports_lead_with_the_result_and_leave_out_team_mechanics() -> None:
    """A task report reads as the result, not as the team's bookkeeping.

    Trial s-7d7b7d22: the chat's task reports said "再次交接被自动审批拒绝…系统的
    最终验收登记尚未确认" and the operator asked "为啥我看不懂你在干啥".
    """
    manager = _BUILTIN_ROOT / "manager"
    for rule in ("replying", "team-handoff"):
        text = " ".join((manager / rule / "SKILL.md").read_text(encoding="utf-8").split())
        assert "one short paragraph" in text, rule
        assert "the result and where to open it" in text, rule
        for mechanic in ("approvals or refusals", "verification records", "certification bookkeeping", "review rounds"):
            assert mechanic in text, (rule, mechanic)
        assert "only when the operator must act" in text, rule
