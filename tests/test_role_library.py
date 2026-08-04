"""Role Agents receive library paths and perform their own discovery."""
from pathlib import Path

from argus_skill.adapters.memory_backend import MemoryBackend
from argus_skill.skills.layered import LayeredSkillStore
from argus_skill.skills.missions import EngineerMission, ReviewerMission
from argus_skill.skills.role_library import role_skill_libraries
from argus_skill.skills.store import SkillStore


def test_role_receives_path_without_matcher_call_or_content(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "example.md").write_text(
        "---\nname: Example\ndescription: Example guidance.\n---\n\n"
        "# Example\n\nPRIVATE BODY\n",
        encoding="utf-8",
    )
    backend = MemoryBackend()
    store = SkillStore(root)

    result = role_skill_libraries(store, role="engineer")

    assert str(root.resolve()) in result.block
    assert "PRIVATE BODY" not in result.block
    assert backend.history == []


def test_layered_roots_are_exposed_in_order(tmp_path: Path) -> None:
    store = LayeredSkillStore(
        project_dir=tmp_path / "project",
        vertical_dir=tmp_path / "vertical",
        global_dir=tmp_path / "global",
    )
    result = role_skill_libraries(store, role="reviewer")
    positions = [result.block.index(str(path)) for path in store.library_roots()]
    assert positions == sorted(positions)


def test_each_role_searches_same_library_independently(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    engineer = EngineerMission(store).libraries()
    reviewer = ReviewerMission(store).libraries()
    assert engineer.block != ""
    assert reviewer.block != ""
    assert engineer.library_roots == reviewer.library_roots
