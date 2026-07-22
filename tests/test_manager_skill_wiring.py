"""Manager ↔ skill library wiring + the planner-role-skill relocation bug.

Covers the three things the operator asked for:

1. The planner role skill now lives in ``builtin_skills/planner/`` (NOT
   ``engineer/``), is still loaded by ``load_builtin_skill_text`` (the planner's
   loader), and ``role_of_path`` no longer misclassifies it as an engineer skill.
2. ``_ROLE_SUBDIRS`` (and the role pools) include ``manager``.
3. The ``Manager`` takes an optional ``skill_store`` and injects its fixed role
   skill (+ any matched adaptive block) into its stage-decision prompt; with no
   ``skill_store`` (the default) the behaviour is unchanged (back-compat).
"""
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
    assert ROLE_SKILL_POOLS["manager"] == frozenset({"manager"})
    # Manager sees every other role's standards as read-only references.
    assert ROLE_CROSS_READ_POOLS["manager"] == frozenset(
        {"engineer", "reviewer", "planner"}
    )


def test_manager_role_skill_file_exists_and_loads() -> None:
    assert (_BUILTIN_ROOT / "manager" / "argus-manager-role.md").is_file()
    text = load_builtin_skill_text("argus-manager-role.md")
    assert "Argus Manager Role" in text


# --------------------------------------------------------------------------
# 3. Manager takes a skill_store and injects skills into its decision prompt
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


def test_manager_decision_prompt_carries_role_skill_when_store_present(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.store import SkillStore

    # Empty store: the matched adaptive block is empty, but the FIXED role skill
    # must still be injected ahead of the stage-decision prompt.
    store = SkillStore(tmp_path / "skills")
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=store)

    cap = _CapturingRunExec()
    decision = mgr.decide_stage_transition(
        review=_StubReview(), project_root=tmp_path, run_exec=cap
    )

    assert cap.prompts, "manager never built a stage-decision prompt"
    prompt = cap.prompts[0]
    # The fixed manager role skill is prepended to the decision prompt.
    assert "Argus manager role skill" in prompt
    assert "Argus Manager Role" in prompt
    # The original decision contract is still present (schema unchanged).
    assert '"action": "advance|hold|rollback"' in prompt
    # The stub returned HOLD → no stage write, decision is a HOLD.
    assert decision.action == "hold"


def test_manager_decision_prompt_unchanged_without_store(tmp_path: Path) -> None:
    # With NO skill_store the decision prompt must NOT carry the role-skill header
    # (byte-for-byte back-compat with the pre-skill Manager).
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=None)
    cap = _CapturingRunExec()
    mgr.decide_stage_transition(
        review=_StubReview(), project_root=tmp_path, run_exec=cap
    )
    assert cap.prompts
    assert "Argus manager role skill" not in cap.prompts[0]


# --------------------------------------------------------------------------
# 4. Manager does NOT inject role skill into front-door classify
# --------------------------------------------------------------------------
class _CapturingRunner:
    """A runner whose ``run_exec`` records the prompt; tolerates the persistent
    session's extra ``resume_thread_id`` kwarg. Used to capture the approve gate's
    prompt at the Manager level (the approval call goes through the session)."""

    def __init__(self, message: str = '{"approve": true, "why": "ok"}') -> None:
        self.message = message
        self.prompts: list[str] = []

    def run_exec(self, *, prompt: str, options=None, run_label: str = "",
                 resume_thread_id=None) -> object:
        self.prompts.append(prompt)

        class _R:
            last_agent_message = self.message
            thread_id = None

        return _R()


def test_manager_classify_prompt_stays_minimal_when_store_present(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=store)

    seen: list[str] = []

    def run_exec(prompt: str) -> object:
        seen.append(prompt)

        class _R:
            last_agent_message = "TEAM"
            exit_code = 0

        return _R()

    mgr.is_conversational("是不是要做点什么", run_exec=run_exec)
    assert seen, "manager never built a classify prompt"
    assert "Argus manager role skill" not in seen[0]
    assert "Argus Manager Role" not in seen[0]
    assert "CHAT" in seen[0] and "TASK" in seen[0]


def test_manager_classify_prompt_unchanged_without_store(tmp_path: Path) -> None:
    from argus_skill.life.router import build_classify_prompt

    mgr = Manager(project_root=tmp_path, runner=object(), skill_store=None)
    seen: list[str] = []

    def run_exec(prompt: str) -> object:
        seen.append(prompt)

        class _R:
            last_agent_message = "TEAM"
            exit_code = 0

        return _R()

    text = "是不是要做点什么"
    mgr.is_conversational(text, run_exec=run_exec)
    assert seen
    # No store → no role-skill header, byte-for-byte the legacy classify prompt.
    assert "Argus manager role skill" not in seen[0]
    assert seen[0] == build_classify_prompt(text)
