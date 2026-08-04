from pathlib import Path

from argus_skill.wiki.bootstrap import init_wiki, is_initialized_wiki


def test_init_creates_minimal_tree(tmp_path: Path) -> None:
    root = init_wiki(project="demo", base=tmp_path)
    assert root == tmp_path / ".autors" / "demo" / "wiki"
    assert (root / "pages").is_dir()
    assert (root / "INDEX.md").is_file()
    assert (root / "README.md").is_file()
    assert is_initialized_wiki(root)
    for removed in ("sources", "queries", "data", "query_pack.md"):
        assert not (root / removed).exists()


def test_init_preserves_agent_index(tmp_path: Path) -> None:
    root = init_wiki(project="demo", base=tmp_path)
    (root / "INDEX.md").write_text("# Agent index\n", encoding="utf-8")
    init_wiki(project="demo", base=tmp_path)
    assert (root / "INDEX.md").read_text(encoding="utf-8") == "# Agent index\n"
