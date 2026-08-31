import os
from pathlib import Path

import pytest

from argus_skill.skills.layered import (
    LAYER_GLOBAL,
    LAYER_PROJECT,
    LAYER_VERTICAL,
    LayeredSkillStore,
    shared_skill_scope_dir,
)
from argus_skill.skills.store import Skill


def _store(tmp_path: Path) -> LayeredSkillStore:
    return LayeredSkillStore(
        project_dir=tmp_path / "project",
        vertical_dir=tmp_path / "vertical",
        global_dir=tmp_path / "global",
    )


def test_library_roots_are_project_vertical_global(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.library_roots() == [
        (tmp_path / "project").resolve(),
        (tmp_path / "vertical").resolve(),
        (tmp_path / "global").resolve(),
    ]


def test_native_project_root_is_read_only_and_does_not_replace_managed_writes(
    tmp_path: Path,
) -> None:
    native_root = tmp_path / "repo" / ".agents" / "skills"
    native_skill = native_root / "local" / "SKILL.md"
    native_skill.parent.mkdir(parents=True)
    native_skill.write_text("native", encoding="utf-8")
    store = LayeredSkillStore(
        project_dir=tmp_path / "managed",
        global_dir=tmp_path / "global",
        native_project_dir=native_root,
    )

    assert store.library_roots()[:2] == [
        native_root.resolve(),
        (tmp_path / "managed").resolve(),
    ]
    managed = store.save(
        Skill("Managed", "One line.", "# Managed", path="engineer/managed.md")
    )
    assert managed == tmp_path / "managed" / "engineer" / "managed.md"
    assert native_skill.read_text(encoding="utf-8") == "native"
    with pytest.raises(PermissionError, match="read-only"):
        store.save(
            Skill(
                "Native",
                "One line.",
                "# Native",
                path=str(native_root / "new" / "SKILL.md"),
            )
        )
    with pytest.raises(PermissionError, match="read-only"):
        store.archive_path(native_skill)


def test_missing_native_project_root_preserves_existing_roots(tmp_path: Path) -> None:
    store = LayeredSkillStore(
        project_dir=tmp_path / "project",
        vertical_dir=tmp_path / "vertical",
        global_dir=tmp_path / "global",
        native_project_dir=tmp_path / "repo" / ".agents" / "skills",
    )

    assert not (tmp_path / "repo" / ".agents").exists()
    assert store.library_roots() == [
        (tmp_path / "project").resolve(),
        (tmp_path / "vertical").resolve(),
        (tmp_path / "global").resolve(),
    ]


def test_native_project_root_symlink_cannot_escape_execution_project(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    external = tmp_path / "external-skills"
    external.mkdir()
    native_root = repo / ".agents" / "skills"
    native_root.parent.mkdir(parents=True)
    try:
        native_root.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    store = LayeredSkillStore(
        project_dir=tmp_path / "managed",
        global_dir=tmp_path / "global",
        native_project_dir=native_root,
        execution_project_root=repo,
    )

    assert external.resolve() not in store.library_roots()


def test_native_project_child_symlink_cannot_escape_execution_project(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    native_root = repo / ".agents" / "skills"
    native_root.mkdir(parents=True)
    external_skill = tmp_path / "external-skill"
    external_skill.mkdir()
    try:
        (native_root / "escape").symlink_to(
            external_skill, target_is_directory=True
        )
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    store = LayeredSkillStore(
        project_dir=tmp_path / "managed",
        global_dir=tmp_path / "global",
        native_project_dir=native_root,
        execution_project_root=repo,
    )

    assert native_root.resolve() not in store.library_roots()


def test_contained_native_project_children_remain_available(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    native_root = repo / ".agents" / "skills"
    skill = native_root / "contained" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("contained", encoding="utf-8")

    store = LayeredSkillStore(
        project_dir=tmp_path / "managed",
        global_dir=tmp_path / "global",
        native_project_dir=native_root,
        execution_project_root=repo,
    )

    assert store.native_project_roots() == [native_root.resolve()]


def test_explicit_semantic_path_selects_existing_layer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    skill = Skill(
        "Shared guidance",
        "One line.",
        "# Shared guidance",
        path=str(tmp_path / "global" / "research" / "shared-guidance.md"),
    )
    path = store.save(skill)
    assert path.is_file()
    assert store.layer_for_path(path) == LAYER_GLOBAL
    assert store.layer_for_path(tmp_path / "vertical" / "x.md") == LAYER_VERTICAL
    assert store.layer_for_path(tmp_path / "project" / "x.md") == LAYER_PROJECT


def test_archive_is_project_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    global_path = store.save(
        Skill(
            "Global",
            "One line.",
            "# Global",
            path=str(tmp_path / "global" / "global.md"),
        )
    )
    with pytest.raises(PermissionError):
        store.archive_path(global_path)


def test_shared_scope_uses_operator_semantic_name(tmp_path: Path) -> None:
    assert shared_skill_scope_dir(tmp_path, "software/backend") == (
        tmp_path / "_shared_verticals" / "software/backend"
    )
    assert shared_skill_scope_dir(tmp_path, "../escape") is None
