"""Reviewed project Wiki pages tagged for a wider audience reach the shared roots."""
from __future__ import annotations

import os
from pathlib import Path

from argus.wiki.promote import promote_wiki_pages


def _page(title: str, description: str, body: str, *, audience: str | None = None) -> str:
    extra = f"audience: {audience}\n" if audience else ""
    return f"---\ntitle: {title}\ndescription: {description}\n{extra}---\n\n{body}\n"


def _project_wiki(workspace: Path, project: str = "proj") -> Path:
    root = workspace / ".autors" / project / "wiki"
    (root / "pages").mkdir(parents=True)
    (root / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    return root


def _write(path: Path, text: str, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_tagged_pages_are_copied_and_listed_once(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "pages" / "eval" / "protocol.md", _page("Protocol", "How we evaluate", "Body.", audience="vertical"), 2_000)
    _write(wiki / "pages" / "hosts.md", _page("Hosts", "Host facts", "Body.", audience="global"), 3_000)
    _write(wiki / "pages" / "local.md", _page("Local", "Project only", "Body."), 4_000)

    promoted = promote_wiki_pages(workspace, vertical="research", shared_root=shared)

    assert promoted == {"vertical": ["eval/protocol.md"], "global": ["hosts.md"]}
    vertical_copy = shared / "_shared_verticals" / "research" / "pages" / "eval" / "protocol.md"
    global_copy = shared / "_global" / "pages" / "hosts.md"
    assert vertical_copy.read_text(encoding="utf-8") == _page("Protocol", "How we evaluate", "Body.", audience="vertical")
    assert global_copy.is_file()
    assert not (shared / "_global" / "pages" / "local.md").exists()
    assert not (shared / "_shared_verticals" / "research" / "pages" / "local.md").exists()
    vertical_index = (shared / "_shared_verticals" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert vertical_index == "# Research knowledge\n\n- [Protocol](pages/eval/protocol.md) — How we evaluate\n"
    global_index = (shared / "_global" / "INDEX.md").read_text(encoding="utf-8")
    assert global_index == "# Global knowledge\n\n- [Hosts](pages/hosts.md) — Host facts\n"

    # Running again changes nothing: the shared copies are as new as the pages.
    again = promote_wiki_pages(workspace, vertical="research", shared_root=shared)
    assert again == {"vertical": [], "global": []}
    assert (shared / "_shared_verticals" / "research" / "INDEX.md").read_text(encoding="utf-8") == vertical_index


def test_newer_project_page_replaces_older_shared_copy_but_not_the_reverse(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    target = shared / "_shared_verticals" / "research" / "pages" / "notes.md"
    _write(target, _page("Notes", "Old", "Old body.", audience="vertical"), 1_000)
    (shared / "_shared_verticals" / "research" / "INDEX.md").write_text(
        "# Research knowledge\n\n- [Notes](pages/notes.md) — Old\n", encoding="utf-8"
    )
    _write(wiki / "pages" / "notes.md", _page("Notes", "New", "New body.", audience="vertical"), 5_000)

    promoted = promote_wiki_pages(workspace, vertical="research", shared_root=shared)

    assert promoted["vertical"] == ["notes.md"]
    assert "New body." in target.read_text(encoding="utf-8")
    # The index already names the page; no second line is added.
    index = (shared / "_shared_verticals" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert index.count("](pages/notes.md)") == 1

    # An older project page never overwrites a newer shared copy.
    _write(wiki / "pages" / "notes.md", _page("Notes", "Stale", "Stale body.", audience="vertical"), 100)
    assert promote_wiki_pages(workspace, vertical="research", shared_root=shared) == {"vertical": [], "global": []}
    assert "New body." in target.read_text(encoding="utf-8")


def test_malformed_or_hidden_pages_are_skipped_without_raising(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "pages" / "broken.md", "---\ntitle: [unclosed\naudience: global\n---\n\nBody.\n", 1_000)
    _write(wiki / "pages" / "untitled.md", "---\ndescription: no title\naudience: global\n---\n\nBody.\n", 1_000)
    _write(wiki / "pages" / "plain.md", "# Plain\n\nNo front matter.\n", 1_000)
    _write(wiki / "pages" / "odd.md", _page("Odd", "Unknown audience", "Body.", audience="team"), 1_000)
    _write(wiki / "pages" / ".draft.md", _page("Draft", "Hidden", "Body.", audience="global"), 1_000)
    _write(wiki / "pages" / "good.md", _page("Good", "Fine", "Body.", audience="global"), 1_000)
    (wiki / "pages" / "escape.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "outside.md").write_text(_page("Outside", "Not a page", "Body.", audience="global"), encoding="utf-8")

    promoted = promote_wiki_pages(workspace, vertical="research", shared_root=shared)

    assert promoted == {"vertical": [], "global": ["good.md"]}
    assert sorted(path.name for path in (shared / "_global" / "pages").iterdir()) == ["good.md"]


def test_vertical_pages_stay_home_when_no_vertical_is_known(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "pages" / "a.md", _page("A", "Vertical page", "Body.", audience="vertical"), 1_000)
    _write(wiki / "pages" / "b.md", _page("B", "Global page", "Body.", audience="global"), 1_000)

    assert promote_wiki_pages(workspace, vertical="", shared_root=shared) == {"vertical": [], "global": ["b.md"]}
    assert promote_wiki_pages(workspace, vertical="../evil", shared_root=shared) == {"vertical": [], "global": []}
    assert not (shared / "_shared_verticals").exists()


def test_workspace_without_a_wiki_promotes_nothing(tmp_path):
    shared = tmp_path / "home" / "wiki"
    assert promote_wiki_pages(tmp_path / "ws", vertical="research", shared_root=shared) == {"vertical": [], "global": []}
    assert not shared.exists()
