"""Behavioral regressions for audited global Skill failures and migration."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from argus.life.event_log import JsonlEventSink
from argus.skills import builtins
from argus.skills.role_library import role_skill_libraries
from argus.skills.store import SkillStore
from argus.tools.subagent import _direct_run


@pytest.mark.parametrize("verbosity,expected", [("full", 1), ("signal", 0)])
def test_documented_audit_reads_compact_json_without_claiming_complete_history(tmp_path, verbosity, expected):
    sink = JsonlEventSink(None, life_dir=tmp_path, verbosity=verbosity)
    (tmp_path / "events.jsonl").touch()
    sink.handle_event({"type": "engineer.progress", "text": "python -m pytest tests/test_example.py"})
    document = dict(builtins.iter_builtin_skill_texts())["reviewer/engineer-process-audit.md"]
    program = re.search(r"<<'PYCODE'\n(.*?)\nPYCODE", document, re.S).group(1)
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path / "events.jsonl")],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert len(report["recorded_progress_tail"]) == expected
    assert report["complete_execution_history"] is False
    assert report["malformed_records"] == 0


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


def test_moved_research_skills_are_available_only_in_their_library_and_supervisor(tmp_path, monkeypatch):
    relative = "engineer/rl-training-collapse-diagnosis.md"
    assert relative not in dict(builtins.iter_builtin_skill_texts())
    source = dict(builtins.iter_vertical_skill_texts("research"))[relative]
    builtins.seed_context_skills(tmp_path, "research")
    assert (tmp_path / relative).read_text() == source
    monkeypatch.setattr(_direct_run, "_RL_COLLAPSE_GUIDANCE_CACHE", None)
    assert _direct_run._rl_collapse_guidance_for("python train_grpo.py") == _direct_run._strip_skill_frontmatter(source).strip()
    assert _direct_run._rl_collapse_guidance_for("python evaluate.py") == ""


def test_vertical_refresh_preserves_learning_and_updates_only_factory_copies(tmp_path, monkeypatch):
    relative = "engineer/rl-training-collapse-diagnosis.md"
    def text(version):
        return f'---\nname: "Run health"\ndescription: "Inspect reward gradients"\n---\n{version}\n'
    monkeypatch.setattr(builtins, "iter_context_skill_texts", lambda *_: [(relative, text("v1")), ("engineer/other.md", text("v1"))])
    monkeypatch.setattr(builtins, "iter_context_skill_assets", lambda *_: [])
    builtins.seed_context_skills(tmp_path, "research")
    edited = tmp_path / relative
    edited.write_text(text("learned exception"))
    monkeypatch.setattr(builtins, "iter_context_skill_texts", lambda *_: [(relative, text("v2")), ("engineer/other.md", text("v2"))])
    builtins.seed_context_skills(tmp_path, "research")
    assert edited.read_text() == text("learned exception")
    assert (tmp_path / "engineer/other.md").read_text() == text("v2")
    assert not (tmp_path / "_retired_builtin_skills").exists()
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    builtins.seed_context_skills(tmp_path, "research")
    assert before == {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}


def test_global_retirement_keeps_an_edited_moved_skill_recoverable(tmp_path):
    path = tmp_path / "engineer/rl-training-collapse-diagnosis.md"
    path.parent.mkdir()
    path.write_text("a project-specific learned correction\n")
    builtins.seed_builtin_skills(tmp_path)
    assert not path.exists()
    archive = tmp_path / "_retired_builtin_skills/engineer/rl-training-collapse-diagnosis.md.retired"
    assert archive.read_text() == "a project-specific learned correction\n"
