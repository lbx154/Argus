from pathlib import Path

from argus.core.event_catalog import EventType
from argus.wiki.bootstrap import init_wiki, is_initialized_wiki
from argus.wiki.lifecycle import ensure_project_wiki


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


def test_runtime_bootstrap_creates_discoverable_wiki_and_emits_entry(
    tmp_path: Path,
) -> None:
    events: list[dict] = []

    root = ensure_project_wiki(tmp_path, enabled=True, on_event=events.append)

    assert root == tmp_path / ".autors" / tmp_path.name / "wiki"
    assert is_initialized_wiki(root)
    assert (root / "INDEX.md").read_text(encoding="utf-8").startswith("# Wiki Index")
    assert events == [
        {
            "type": EventType.WIKI_INITIALIZED,
            "project": tmp_path.name,
            "path": str(root),
            "auto": True,
            "text": f"initialized project Wiki at {root}",
        }
    ]
