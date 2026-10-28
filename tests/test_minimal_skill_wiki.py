from pathlib import Path

import yaml

from argus.wiki.bootstrap import init_wiki, is_initialized_wiki
from argus.wiki.schema import WikiPage, parse_page
from argus.wiki.store import WikiStore


def test_wiki_page_format(tmp_path: Path) -> None:
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
    assert not (root / "sources").exists()
    assert not (root / "queries").exists()
    assert not (root / "data").exists()


def test_source_controlled_skills_use_only_minimal_frontmatter() -> None:
    package_root = Path(__file__).resolve().parents[1] / "argus"
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


def test_wiki_description_falls_back_to_title_when_body_has_no_prose() -> None:
    text = "---\ntitle: Only a heading\nstatus: active\n---\n\n# Only a heading\n"
    assert parse_page(text).description == "Only a heading"


def test_wiki_description_skips_fenced_code() -> None:
    text = (
        "---\ntitle: Fenced\n---\n\n"
        "# Fenced\n\n```text\nnot_a_description=1\n```\n\nReal prose here.\n"
    )
    assert parse_page(text).description == "Real prose here."
