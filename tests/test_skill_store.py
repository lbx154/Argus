from pathlib import Path

import pytest

from argus_skill.skills.store import Skill, SkillStore


def test_save_requires_explicit_semantic_path(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    with pytest.raises(ValueError, match="semantic Skill path"):
        store.save(Skill("Name", "Description", "# Name"))


def test_save_writes_only_minimal_frontmatter(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    path = store.save(
        Skill(
            "Parser return contract",
            "Preserve the caller-visible return shape.",
            "# Parser return contract\n\nInspect every call site.",
            path="parser/return-contract.md",
        )
    )
    text = path.read_text(encoding="utf-8")
    front = text[4:].split("\n---\n", 1)[0]
    assert [line.split(":", 1)[0] for line in front.splitlines()] == [
        "name",
        "description",
    ]
    assert "# Parser return contract" in text
    assert not hasattr(Skill, "parse")


def test_path_listing_never_reads_or_parses_documents(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    path = tmp_path / "domain" / "malformed-but-agent-readable.md"
    path.parent.mkdir()
    path.write_text("arbitrary markdown", encoding="utf-8")
    rows = store.list_summaries()
    assert rows == [
        {
            "name": "domain/malformed-but-agent-readable",
            "description": "",
            "path": str(path.resolve()),
            "role": "general",
        }
    ]


def test_archive_preserves_semantic_relative_path(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    path = store.save(
        Skill("Name", "Description", "# Name", path="domain/name.md")
    )
    archived = store.archive_path(path)
    assert archived == tmp_path / "_archive" / "domain" / "name.md"
    assert archived.is_file()
