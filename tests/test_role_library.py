"""Role Agents receive bounded recall plus library paths for discovery."""
from pathlib import Path

from argus_skill.adapters.memory_backend import MemoryBackend
from argus_skill.core.event_catalog import validate_event_envelope
from argus_skill.skills.layered import LayeredSkillStore
from argus_skill.skills.missions import (
    EngineerMission,
    ManagerMission,
    PlannerMission,
    ReviewerMission,
)
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
    assert "before repository work" in result.block
    assert "make one native Skill decision" in result.block
    assert backend.history == []


def test_relevant_skill_is_recalled_into_mandatory_context(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "engineer" / "runtime" / "aiter-cache.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        'name: "Persist AITER compilation"\n'
        'description: "Reuse AITER JIT artifacts for ROCm model startup."\n'
        "---\n\n"
        "# AITER cache\n\nSet `AITER_JIT_DIR` before launching vLLM.\n",
        encoding="utf-8",
    )

    result = role_skill_libraries(
        SkillStore(root),
        role="engineer",
        task="Speed up ROCm vLLM startup by avoiding repeated AITER compilation.",
    )

    assert result.recalled_paths == [skill.resolve()]
    assert "Recalled Skills — mandatory execution context" in result.block
    assert "Set `AITER_JIT_DIR` before launching vLLM." in result.block


def test_irrelevant_skill_body_is_not_injected(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "engineer" / "database-migration.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        'name: "Database migration"\n'
        'description: "Apply PostgreSQL schema migrations safely."\n'
        "---\n\n"
        "PRIVATE DATABASE BODY\n",
        encoding="utf-8",
    )

    result = role_skill_libraries(
        SkillStore(root),
        role="engineer",
        task="Optimize ROCm model compilation.",
    )

    assert result.recalled_paths == []
    assert "PRIVATE DATABASE BODY" not in result.block


def test_project_agent_skills_are_first_for_every_runtime_role(tmp_path: Path) -> None:
    native_root = tmp_path / "repo" / ".agents" / "skills"
    skill_file = native_root / "local-proof" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: local-proof\ndescription: Proves native discovery.\n---\n\n"
        "PRIVATE NATIVE BODY\n",
        encoding="utf-8",
    )
    store = LayeredSkillStore(
        project_dir=tmp_path / "managed",
        vertical_dir=tmp_path / "vertical",
        global_dir=tmp_path / "global",
        native_project_dir=native_root,
    )

    for mission_type in (
        PlannerMission,
        ManagerMission,
        EngineerMission,
        ReviewerMission,
    ):
        libraries = mission_type(store).libraries()
        assert libraries.native_paths[0] == native_root.resolve()
        assert libraries.library_roots[0] == native_root.resolve()
        assert str(native_root.resolve()) in libraries.block
        assert "PRIVATE NATIVE BODY" not in libraries.block


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
    (store.skills_dir / "engineer").mkdir()
    (store.skills_dir / "reviewer").mkdir()
    engineer = EngineerMission(store).libraries()
    reviewer = ReviewerMission(store).libraries()
    assert engineer.block != ""
    assert reviewer.block != ""
    assert engineer.library_roots == reviewer.library_roots
    assert "OWN: engineer, root" in engineer.block
    assert "REFERENCE only: reviewer, self" in engineer.block
    assert "OWN: root, reviewer" in reviewer.block
    assert "REFERENCE only: engineer, self" in reviewer.block
    assert engineer.native_paths == [
        store.skills_dir.resolve() / "engineer",
        store.skills_dir.resolve() / "reviewer",
    ]
    assert reviewer.native_paths == [
        store.skills_dir.resolve() / "reviewer",
        store.skills_dir.resolve() / "engineer",
    ]


def test_self_and_team_role_libraries_are_cross_visible(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    for role in ("self", "manager", "planner", "engineer", "reviewer"):
        (store.skills_dir / role).mkdir()

    self_path = store.skills_dir.resolve() / "self"
    for role in ("manager", "planner", "engineer", "reviewer"):
        libraries = role_skill_libraries(store, role=role)
        assert self_path in libraries.reference_paths
        assert self_path in libraries.native_paths

    self_libraries = role_skill_libraries(store, role="self")
    assert store.skills_dir.resolve() / "manager" in self_libraries.own_paths
    assert store.skills_dir.resolve() / "engineer" in self_libraries.own_paths
    assert store.skills_dir.resolve() / "planner" in self_libraries.reference_paths
    assert store.skills_dir.resolve() / "reviewer" in self_libraries.reference_paths


def test_general_native_root_requires_a_direct_skill(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    assert role_skill_libraries(store, role="engineer").native_paths == []

    (store.skills_dir / "general-guidance.md").write_text("guidance", encoding="utf-8")

    assert role_skill_libraries(store, role="engineer").native_paths == [
        store.skills_dir.resolve()
    ]


def test_general_skill_is_visible_to_every_runtime_role(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    common = store.skills_dir / "agent-team-lead.md"
    common.write_text("shared Team contract", encoding="utf-8")

    for role in ("self", "manager", "planner", "engineer", "reviewer"):
        libraries = role_skill_libraries(store, role=role)
        assert store.skills_dir.resolve() in libraries.own_paths
        assert store.skills_dir.resolve() in libraries.native_paths


def test_role_library_event_exposes_precedence_without_skill_content(
    tmp_path: Path,
) -> None:
    store = SkillStore(tmp_path / "skills")
    (store.skills_dir / "planner").mkdir()
    events: list[dict] = []

    result = role_skill_libraries(store, role="planner", on_event=events.append)

    assert result.own_paths == [store.skills_dir.resolve() / "planner"]
    assert events[0]["precedence"] == ["project", "vertical", "global"]
    assert events[0]["discovery"] == "native-or-path-fallback"
    assert validate_event_envelope(events[0]).valid
    assert "Skill body" not in str(events[0])


def test_required_skill_path_is_resolved_and_emitted_without_body(
    tmp_path: Path,
) -> None:
    store = SkillStore(tmp_path / "skills")
    required = store.skills_dir / "engineer" / "idea-discovery.md"
    required.parent.mkdir()
    required.write_text("PRIVATE REQUIRED BODY", encoding="utf-8")
    events: list[dict] = []

    result = role_skill_libraries(
        store,
        role="engineer",
        on_event=events.append,
        required_relative_paths=("engineer/idea-discovery.md",),
    )

    assert result.required_paths == [required.resolve()]
    assert events[0]["required_paths"] == [str(required.resolve())]
    assert str(required.resolve()) in result.block
    assert "PRIVATE REQUIRED BODY" not in result.block
