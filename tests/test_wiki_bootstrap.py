from __future__ import annotations

from pathlib import Path

from argus_skill.wiki.bootstrap import init_wiki


def test_init_creates_full_tree(tmp_path: Path):
    root = init_wiki(project="demo", base=tmp_path)
    assert root == tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/runs",
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
