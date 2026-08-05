from pathlib import Path

import pytest
import yaml

from argus_skill.skills.role_library import role_skill_libraries
from argus_skill.skills.store import Skill, SkillStore
from argus_skill.wiki.bootstrap import init_wiki, is_initialized_wiki
from argus_skill.wiki.index import rebuild_indexes
from argus_skill.wiki.schema import WikiPage, parse_page, serialize_page
from argus_skill.wiki.store import WikiStore


def test_skill_format_has_only_name_description_and_body(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    path = store.save(
        Skill(
            name="Bounded output retry",
            description="Avoid an unbounded output retry after input failure.",
            content="# Bounded output retry\n\nRead the transport contract.",
            path="ansible/connection/winrm/bounded-output-retry.md",
        )
    )
    text = path.read_text(encoding="utf-8")
    front = text[4:].split("\n---\n", 1)[0]
    assert [line.split(":", 1)[0] for line in front.splitlines()] == [
        "name",
        "description",
    ]
    assert "# Bounded output retry" in text
    assert not hasattr(Skill, "parse")


def test_skill_store_requires_agent_authored_semantic_path(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    with pytest.raises(ValueError, match="semantic Skill path"):
        store.save(Skill("Name", "Description", "# Body"))


def test_role_receives_paths_not_skill_content(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.save(
        Skill(
            "Private body",
            "Description",
            "# Private body\n\nDO NOT PRELOAD THIS BODY",
            path="project/private-body.md",
        )
    )
    result = role_skill_libraries(store, role="engineer")
    assert str(store.skills_dir.resolve()) in result.block
    assert "DO NOT PRELOAD THIS BODY" not in result.block


def test_wiki_page_format_and_single_index(tmp_path: Path) -> None:
    root = init_wiki("openlibrary", base=tmp_path)
    assert is_initialized_wiki(root)
    store = WikiStore(root)
    page_path = store.write_page(
        "catalog-import/marc/alternate-script-linkage.md",
        WikiPage(
            title="MARC alternate-script linkage",
            description="Explains linkage between primary and alternate-script fields.",
            content="# MARC alternate-script linkage\n\nSubfield 6 carries linkage.",
        ),
    )
    loaded = parse_page(page_path.read_text(encoding="utf-8"))
    assert loaded.title == "MARC alternate-script linkage"
    assert set((page_path.read_text().split("\n---\n", 1)[0][4:]).splitlines()[0].split())
    rebuild_indexes(store)
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "pages/catalog-import/marc/alternate-script-linkage.md" in index
    assert not (root / "sources").exists()
    assert not (root / "queries").exists()
    assert not (root / "data").exists()


def test_source_controlled_skills_use_only_minimal_frontmatter() -> None:
    package_root = Path(__file__).resolve().parents[1] / "argus_skill"
    roots = [
        package_root / "builtin_skills",
        package_root / "verticals",
        package_root / "domains",
    ]
    checked = 0
    for root in roots:
        for path in root.rglob("*.md"):
            if root.name in {"verticals", "domains"} and "skills" not in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                continue  # supporting reference Markdown, not a Skill document
            front = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
            assert set(front) == {"name", "description"}, path
            checked += 1
    assert checked > 100


def test_wiki_writes_only_minimal_frontmatter() -> None:
    text = serialize_page(WikiPage("Title", "Description", "# Title"))
    front = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
    assert set(front) == {"title", "description"}


def test_wiki_reads_legacy_pages_instead_of_dropping_them() -> None:
    """Pages predating the two-field format carry a richer frontmatter and no
    description. Rejecting them emptied every existing knowledge base."""
    legacy = (
        "---\n"
        "id: erdos-straus-typeii\n"
        "type: pattern\n"
        "status: superseded-by-witness\n"
        "title: Type II residue reduction\n"
        "tags: [number-theory]\n"
        "sources: []\n"
        "---\n"
        "\n"
        "# Type II residue reduction\n"
        "\n"
        "Reduces the residue class before the lift gate.\n"
    )
    page = parse_page(legacy)
    assert page.title == "Type II residue reduction"
    assert page.description == "Reduces the residue class before the lift gate."
    assert "lift gate" in page.content


def test_wiki_description_falls_back_to_title_when_body_has_no_prose() -> None:
    text = "---\ntitle: Only a heading\nstatus: active\n---\n\n# Only a heading\n"
    assert parse_page(text).description == "Only a heading"


def test_wiki_description_skips_fenced_code() -> None:
    text = (
        "---\ntitle: Fenced\n---\n\n"
        "# Fenced\n\n```text\nnot_a_description=1\n```\n\nReal prose here.\n"
    )
    assert parse_page(text).description == "Real prose here."


def test_wiki_still_requires_a_title() -> None:
    with pytest.raises(ValueError, match="title"):
        parse_page("---\nstatus: active\n---\n\nBody\n")
