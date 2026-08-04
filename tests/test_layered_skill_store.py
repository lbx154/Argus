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
