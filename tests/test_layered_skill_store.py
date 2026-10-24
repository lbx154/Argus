import os
from pathlib import Path

import pytest

from argus.skills.layered import (
    LayeredSkillStore,
)
from argus.skills.store import Skill


def _store(tmp_path: Path) -> LayeredSkillStore:
    return LayeredSkillStore(
        project_dir=tmp_path / "project",
        vertical_dir=tmp_path / "vertical",
        global_dir=tmp_path / "global",
    )


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
        execution_project_root=tmp_path / "repo",
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
        execution_project_root=repo,
    )

    assert external.resolve() not in store.library_roots()


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
