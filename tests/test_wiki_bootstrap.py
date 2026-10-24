from pathlib import Path

from argus.wiki.bootstrap import init_wiki


def test_init_preserves_agent_index(tmp_path: Path) -> None:
    root = init_wiki(project="demo", base=tmp_path)
    (root / "INDEX.md").write_text("# Agent index\n", encoding="utf-8")
    init_wiki(project="demo", base=tmp_path)
    assert (root / "INDEX.md").read_text(encoding="utf-8") == "# Agent index\n"
