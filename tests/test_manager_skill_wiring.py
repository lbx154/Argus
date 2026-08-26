"""Manager wiring for role-owned, on-demand Skill discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

import argus_skill.builtin_skills as _builtin
from argus_skill.core.models import RunnerResult
from argus_skill.manager import Manager
from argus_skill.skills.role_context import load_builtin_skill_text
from argus_skill.skills.store import (
    _ROLE_SUBDIRS,
    ROLE_CROSS_READ_POOLS,
    ROLE_SKILL_POOLS,
    role_of_path,
)

_BUILTIN_ROOT = Path(_builtin.__file__).parent


# --------------------------------------------------------------------------
# 1. planner role skill relocation (the bug)
# --------------------------------------------------------------------------
def test_planner_role_skill_moved_to_planner_dir() -> None:
    assert (_BUILTIN_ROOT / "planner" / "argus-planner-role.md").is_file()
    assert not (_BUILTIN_ROOT / "engineer" / "argus-planner-role.md").exists()


def test_planner_role_skill_still_loads_from_new_location() -> None:
    # Bare role filenames resolve across bundled role directories.
    text = load_builtin_skill_text("argus-planner-role.md")
    assert "Argus Planner Role" in text


def test_missing_required_role_skill_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError, match="required bundled role skill"):
        load_builtin_skill_text("missing-role-skill.md")


def test_planner_role_skill_no_longer_classified_as_engineer() -> None:
    p = _BUILTIN_ROOT / "planner" / "argus-planner-role.md"
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


def test_manager_role_skill_file_exists_and_loads() -> None:
    assert (_BUILTIN_ROOT / "manager" / "argus-manager-role.md").is_file()
    text = load_builtin_skill_text("argus-manager-role.md")
    compact = " ".join(text.split())
    assert "Argus Manager Role" in text
    assert "Runtime maintenance must use an isolated worktree" in text
    assert "controlled canary" in text
    assert "Publishing that repair is optional" in compact
    assert "never automatic" in compact


# --------------------------------------------------------------------------
# 3. Manager receives paths without preloading a Skill body
# --------------------------------------------------------------------------
class _StubReview:
    status = "continue"
    reason = "checklist not yet satisfied"
    checklist: list = []
    planner_report: dict = {}


class _CapturingRunExec:
    """Captures the prompt passed to the manager's stage-decision LLM call and
    returns a HOLD verdict so no stage write happens."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> RunnerResult:
        self.prompts.append(prompt)
        return RunnerResult(
            exit_code=0,
            agent_messages=['{"action": "hold", "target_stage": "research", "reason": "stub"}'],
        )


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
    from argus_skill.skills.store import Skill, SkillStore

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

    cap = _CapturingRunExec()
    decision = mgr.decide_stage_transition(
        review=_StubReview(), project_root=tmp_path, run_exec=cap
    )

    assert cap.prompts, "manager never built a stage-decision prompt"
    prompt = cap.prompts[0]
    assert str(store.skills_dir.resolve()) in prompt
    assert "Role: manager" in prompt
    assert "DO NOT PRELOAD THIS MANAGER BODY" not in prompt
    assert "Argus Manager Role" not in prompt
    assert "ARGUS_ROLE_DECISION=" not in prompt
    assert "ACTION=hold" in prompt
    assert "Right-sidebar presentation" not in prompt
    assert "LIVE_VIEW_PATHS" not in prompt
    assert decision.action == "hold"


def test_manager_decision_prompt_unchanged_without_store(tmp_path: Path) -> None:
    # With NO skill_store the decision prompt must NOT carry the role-skill header
    # (byte-for-byte back-compat with the pre-skill Manager).
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=None)
    cap = _CapturingRunExec()
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
