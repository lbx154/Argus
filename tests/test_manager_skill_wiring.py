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
)

_BUILTIN_ROOT = Path(_builtin.__file__).parent


# --------------------------------------------------------------------------
# 1. planner role skill relocation (the bug)
# --------------------------------------------------------------------------
def test_planner_role_skill_moved_to_planner_dir() -> None:
    assert (_BUILTIN_ROOT / "planner" / "dependency-aware-task-decomposition.md").is_file()
    assert not (_BUILTIN_ROOT / "engineer" / "argus-planner-role.md").exists()
    assert not (_BUILTIN_ROOT / "planner" / "argus-planner-role.md").exists()


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
