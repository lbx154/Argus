"""Manager skill tidy-up: placement judge + write distilled skills back to the
argus SOURCE tree (builtin / vertical) and commit.

Covers the "janitor" path: after a mission the Manager reviews the runtime
library's distilled skills and writes the new ones (not already in source) into
``builtin_skills/`` (cross-domain) or ``verticals/<v>/skills/`` (domain), then
commits. All source-writing tests isolate ``builtin_skill_source_path`` /
``vertical_skill_source_path`` to a tmp dir so the real argus repo is untouched.
"""
from __future__ import annotations

import subprocess

import pytest

import argus_skill.skills.builtins as builtins_mod
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.manager import skill_tidy
from argus_skill.manager.skill_review import (
    PlacementVerdict,
    approve_skill,
    classify_skill_placement,
)
from argus_skill.skills.store import Skill, SkillStore


def _runner(message: str) -> MemoryBackend:
    return MemoryBackend(default=CannedResponse(message=message))


class _CapturingRunner:
    """Records the prompt passed to ``run_exec`` and returns a canned verdict so
    the approval gate's prompt can be asserted byte-for-byte."""

    def __init__(self, message: str = '{"approve": true, "why": "ok"}') -> None:
        self.message = message
        self.prompts: list[str] = []

    def run_exec(self, *, prompt: str, options=None, run_label: str = "") -> object:
        self.prompts.append(prompt)

        class _R:
            last_agent_message = self.message

        return _R()


def _skill(name: str, *, provisional: bool = False, tasks: int = 2) -> Skill:
    return Skill(
        name=name,
        description=f"do {name}",
        category="x",
        content="## When to use\n- x tasks\n\n## How to solve\n- step 1\n",
        version=1,
        created_at="2026-05-03T00:00:00+00:00",
        provisional=provisional,
        # task_history dedupes by distinct task; ``tasks`` controls how many
        # distinct tasks this skill has proven on (the promotion-gate signal).
        task_history=[f"task {name} {i}" for i in range(tasks)],
    )


@pytest.fixture
def isolated_source(tmp_path, monkeypatch):
    """Point the argus source paths at a tmp git repo; return (root, builtin)."""
    root = tmp_path / "argus_skill"
    builtin = root / "builtin_skills"
    (builtin / "engineer").mkdir(parents=True)
    (builtin / "reviewer").mkdir(parents=True)
    monkeypatch.setattr(builtins_mod, "builtin_skill_source_path", lambda: builtin)
    monkeypatch.setattr(
        builtins_mod,
        "vertical_skill_source_path",
        lambda v: root / "verticals" / v / "skills",
    )
    # A real git repo so commit_to_source can actually commit.
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    # Auto-commit is opt-in (default OFF so a real mission never commits to the
    # operator's repo); these source-writing tests exercise the commit path.
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "1")
    return root, builtin


# --- classify_skill_placement: fail-soft + candidate guard ------------------


def test_placement_global() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"global","vertical":"","why":"general"}'),
    )
    assert v.placement == "global"


def test_placement_vertical_in_candidates() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"vertical","vertical":"quant","why":"factor"}'),
    )
    assert v.placement == "vertical" and v.vertical == "quant"


def test_placement_vertical_not_in_candidates_falls_to_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"vertical","vertical":"bogus","why":"x"}'),
    )
    assert v.placement == "stay"


def test_placement_no_runner_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"], runner=None,
    )
    assert v.placement == "stay"


def test_placement_unparseable_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner("not json"),
    )
    assert v.placement == "stay"


# --- approve_skill: role_skill_block injection (Manager wires role skill) ----


def test_approve_prompt_byte_identical_when_block_empty() -> None:
    # Default empty role_skill_block → the judge prompt is byte-for-byte the
    # legacy one (full back-compat for every caller that does not pass a block).
    default = _CapturingRunner()
    explicit = _CapturingRunner()
    approve_skill(content="some playbook", task="a task", runner=default)
    approve_skill(
        content="some playbook", task="a task", runner=explicit, role_skill_block=""
    )
    assert default.prompts and explicit.prompts
    assert default.prompts[0] == explicit.prompts[0]


def test_approve_prompt_prepends_non_empty_block() -> None:
    block = "ROLE_SKILL_SENTINEL\n\n"
    cap = _CapturingRunner()
    approve_skill(
        content="some playbook", task="a task", runner=cap, role_skill_block=block
    )
    assert cap.prompts
    prompt = cap.prompts[0]
    # The block is prepended verbatim, ahead of the gate's own instructions.
    assert prompt.startswith(block)
    assert "ROLE_SKILL_SENTINEL" in prompt
    # And the rest is exactly the legacy prompt (block + legacy == new).
    legacy = _CapturingRunner()
    approve_skill(content="some playbook", task="a task", runner=legacy)
    assert prompt == block + legacy.prompts[0]


def test_approve_signature_has_role_skill_block() -> None:
    import inspect

    assert "role_skill_block" in inspect.signature(approve_skill).parameters


# --- write_skill_to_source: lands in the right source dir -------------------

def test_write_skill_to_builtin(isolated_source) -> None:
    _root, builtin = isolated_source
    dest = skill_tidy.write_skill_to_source(_skill("gen"), "global", role="engineer")
    assert dest == builtin / "engineer" / "gen.md"
    assert dest.exists() and "do gen" in dest.read_text(encoding="utf-8")


def test_write_skill_to_vertical(isolated_source) -> None:
    root, _builtin = isolated_source
    dest = skill_tidy.write_skill_to_source(
        _skill("factor lens"), "vertical", vertical="quant", role="reviewer"
    )
    assert dest == root / "verticals" / "quant" / "skills" / "reviewer" / "factor-lens.md"
    assert dest.exists()


def test_write_skill_invalid_vertical_returns_none(isolated_source) -> None:
    assert skill_tidy.write_skill_to_source(
        _skill("x"), "vertical", vertical="bogus"
    ) is None
    # research has no skill dir either
    assert skill_tidy.write_skill_to_source(
        _skill("x"), "vertical", vertical="research"
    ) is None


# --- tidy_runtime_skills_to_source: route + skip factory/provisional --------


def test_tidy_routes_to_source_and_commits(isolated_source, tmp_path, monkeypatch) -> None:
    root, builtin = isolated_source
    # Only "factory-one" counts as already-in-source; the rest are new.
    monkeypatch.setattr(skill_tidy, "_collect_source_skill_names", lambda: {"factory-one"})

    runtime = SkillStore(tmp_path / "runtime")
    runtime.save(_skill("gen one"))
    runtime.save(_skill("factor one"))
    runtime.save(_skill("factory-one"))            # already in source → skip
    runtime.save(_skill("unproven", provisional=True))  # provisional → skip

    def classify(*, content, task):
        if "factor" in task:
            return PlacementVerdict("vertical", "quant", "factor")
        return PlacementVerdict("global", "", "general")

    counts = skill_tidy.tidy_runtime_skills_to_source(runtime, classify)
    assert counts["to_builtin"] == 1
    assert counts["to_vertical"] == 1
    assert counts["errors"] == 0
    assert (builtin / "gen-one.md").exists()
    assert (root / "verticals" / "quant" / "skills" / "factor-one.md").exists()
    # factory + provisional were not written
    assert not (builtin / "factory-one.md").exists()
    # a commit was produced with the two new files
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"],
        capture_output=True, text=True,
    ).stdout
    assert "[manager]" in log
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    assert status.strip() == ""  # everything committed


def test_promotion_gate_keeps_one_off_skills_project_local(
    isolated_source, tmp_path, monkeypatch
) -> None:
    """A skill proven on only ONE task must NOT persist into the argus repo — it
    stays in the project/runtime store. Only multi-task-validated skills promote."""
    _root, builtin = isolated_source
    monkeypatch.setattr(skill_tidy, "_collect_source_skill_names", lambda: set())

    runtime = SkillStore(tmp_path / "runtime")
    runtime.save(_skill("one off", tasks=1))     # 1 task → gated, stays
    runtime.save(_skill("proven gen", tasks=3))  # 3 tasks → promotes

    def classify(*, content, task):
        return PlacementVerdict("global", "", "general")

    counts = skill_tidy.tidy_runtime_skills_to_source(runtime, classify)
    assert counts["to_builtin"] == 1            # only the proven one
    assert counts["stayed"] == 1                # the one-off was held back
    assert (builtin / "proven-gen.md").exists()
    assert not (builtin / "one-off.md").exists()


def test_commit_to_source_failsoft_non_git(tmp_path, monkeypatch) -> None:
    # builtin path under a NON-git dir → commit returns False, no raise.
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "1")  # opt in, so we reach the git logic
    builtin = tmp_path / "nogit" / "builtin_skills"
    builtin.mkdir(parents=True)
    monkeypatch.setattr(builtins_mod, "builtin_skill_source_path", lambda: builtin)
    f = builtin / "x.md"
    f.write_text("x", encoding="utf-8")
    assert skill_tidy.commit_to_source([f], "msg") is False


def test_tidy_after_mission_failsoft_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    counts = skill_tidy.tidy_after_mission(tmp_path, runner=None)
    assert counts == {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}
