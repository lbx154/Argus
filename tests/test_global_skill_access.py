"""Fresh projects and every role retain real access to packaged global skills."""
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_PI
from argus.skills import role_library
from argus.skills.builtins import builtin_skill_source_path
from argus.skills.layered import LayeredSkillStore
from argus.skills.store import SkillStore


@pytest.mark.parametrize("role", ["self", "manager", "planner", "engineer", "reviewer"])
@pytest.mark.parametrize("layered", [False, True])
def test_empty_profile_exposes_global_skills_to_each_role_and_pi(tmp_path, role, layered):
    shared = tmp_path / "global"
    store = (LayeredSkillStore(project_dir=tmp_path / "project", global_dir=shared)
             if layered else SkillStore(shared))
    bundled = builtin_skill_source_path().resolve()
    required = "agent-team-lead.md"
    assert (bundled / required).is_file()
    libraries = role_library.role_skill_libraries(store, role=role, task="Hello", required_relative_paths=(required,))
    assert libraries.library_roots[-1] == bundled
    assert libraries.required_paths == [bundled / required]
    assert bundled in libraries.native_paths
    assert "Global Skills are available to every task" in libraries.block
    assert not list(shared.iterdir()), "Discovery should not seed or modify the shared profile"
    command = AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)._build_command(
        resume_thread_id=None, options=RunnerOptions(skill_paths=[str(p) for p in libraries.native_paths]),
    )
    assert str(bundled) in [command[index + 1] for index, value in enumerate(command) if value == "--skill"]


@pytest.mark.parametrize("task", ["hello", "code a website", "prove a theorem", "plan a holiday"])
def test_switching_projects_and_tasks_cannot_remove_global_access(tmp_path, task):
    for project in ("one", "two"):
        store = LayeredSkillStore(project_dir=tmp_path / project, global_dir=tmp_path / "shared")
        libraries = role_library.role_skill_libraries(store, role="manager", task=task)
        assert builtin_skill_source_path() in libraries.library_roots
        assert (builtin_skill_source_path() / "agent-team-lead.md").read_text()


def test_saved_overrides_win_without_copying_or_recalling_the_bundled_body(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    relative = Path("engineer/override.md")
    (bundled / relative).parent.mkdir(parents=True)
    (bundled / relative).write_text('---\nname: Cache\ndescription: AITER compilation cache.\n---\n\nOLD PACKAGED BODY\n')
    monkeypatch.setattr(role_library, "builtin_skill_source_path", lambda: bundled)
    store = LayeredSkillStore(project_dir=tmp_path / "project", vertical_dir=tmp_path / "vertical", global_dir=tmp_path / "global")
    for owner in (store.global_, store.vertical, store.project):
        target = owner.skills_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('---\nname: Cache\ndescription: AITER compilation cache.\n---\n\nSAVED OVERRIDE\n')
        libraries = role_library.role_skill_libraries(store, role="engineer", task="AITER compilation cache", required_relative_paths=(relative.as_posix(),))
        assert libraries.required_paths == [target]
        assert libraries.recalled_paths == []
        assert str(target) in libraries.block
        assert "SAVED OVERRIDE" not in libraries.block
        assert "OLD PACKAGED BODY" not in libraries.block
    assert "OLD PACKAGED BODY" in (bundled / relative).read_text()


def test_native_loader_receives_saved_reference_skills_before_recursive_defaults(tmp_path):
    store = SkillStore(tmp_path / "shared")
    reference = store.skills_dir / "reviewer"
    reference.mkdir()
    libraries = role_library.role_skill_libraries(store, role="engineer")
    assert libraries.native_paths.index(reference) < libraries.native_paths.index(builtin_skill_source_path())
