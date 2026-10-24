"""Behavioral regressions for audited global Skill failures and migration."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.skills import builtins
from argus.skills.role_library import role_skill_libraries
from argus.skills.store import SkillStore


@pytest.mark.parametrize("task", [
    "修复登录页面样式，最小修改，不要增加依赖。",
    "Fix a CSS layout bug on the login page; preserve existing behavior.",
    "Implement a website in React and TypeScript with minimal code.",
    "检查全局技能质量，重点看错误、重复和过度泛化。",
])
def test_discovery_never_reads_or_preinjects_bodies_based_on_keyword_overlap(tmp_path, monkeypatch, task):
    store = SkillStore(tmp_path / "skills")
    skill = store.skills_dir / "engineer" / "web-layout.md"
    skill.parent.mkdir()
    skill.write_text('---\nname: "Web CSS layout"\ndescription: "Fix login page styles 修复网页样式"\n---\nNEVER INJECT THIS BODY\n')

    def no_body_read(*_args, **_kwargs):
        pytest.fail("Library construction must not read Skill bodies")

    monkeypatch.setattr(Path, "read_text", no_body_read)
    result = role_skill_libraries(store, role="engineer", task=task,
                                 required_relative_paths=("engineer/web-layout.md",))
    assert result.recalled_paths == []
    assert result.required_paths == [skill]
    assert skill.parent in result.native_paths
    assert builtins.builtin_skill_source_path() in result.library_roots
    assert "NEVER INJECT" not in result.block


def test_moved_research_skills_are_available_only_in_their_library(tmp_path):
    relative = "engineer/rl-training-collapse-diagnosis.md"
    assert relative not in dict(builtins.iter_builtin_skill_texts())
    source = dict(builtins.iter_vertical_skill_texts("research"))[relative]
    builtins.seed_context_skills(tmp_path, "research")
    assert (tmp_path / relative).read_text() == source
