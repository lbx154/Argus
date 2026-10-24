from pathlib import Path

import pytest

from argus.skills.store import Skill, SkillStore


def test_save_requires_explicit_semantic_path(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    with pytest.raises(ValueError, match="semantic Skill path"):
        store.save(Skill("Name", "Description", "# Name"))


def test_archive_preserves_semantic_relative_path(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    path = store.save(
        Skill("Name", "Description", "# Name", path="domain/name.md")
    )
    archived = store.archive_path(path)
    assert archived == tmp_path / "_archive" / "domain" / "name.md"
    assert archived.is_file()
