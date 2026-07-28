from __future__ import annotations

from pathlib import Path

from argus_skill.wiki.bootstrap import init_wiki, is_initialized_wiki


def test_init_creates_full_tree(tmp_path: Path):
    root = init_wiki(project="demo", base=tmp_path)
    assert root == tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/notes",
        "pages/concepts",
        "pages/principles",
        "pages/facts",
        "pages/hypotheses",
        "pages/relationships",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "queries",
        "data",
        "scripts",
    ):
        assert (root / sub).is_dir(), f"missing {sub}"
    assert (root / "data" / "schema.yaml").read_text().startswith("# argus-skill")
    assert (root / "data" / "tags.yaml").exists()
    assert (root / "query_pack.md").exists()
    assert (root / "README.md").exists()


def test_init_is_idempotent(tmp_path: Path):
    init_wiki(project="demo", base=tmp_path)
    # second call must not raise and must not overwrite user edits
    (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").write_text("user edit")
    init_wiki(project="demo", base=tmp_path)
    assert (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").read_text() == (
        "user edit"
    )


def test_legacy_wiki_without_new_page_kind_dirs_remains_initialized(tmp_path: Path):
    root = tmp_path / ".autors" / "legacy" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/notes",
        "pages/techniques",
        "queries",
        "data",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "data" / "schema.yaml").write_text("# legacy\n")
    (root / "query_pack.md").write_text("# legacy\n")

    assert is_initialized_wiki(root)
