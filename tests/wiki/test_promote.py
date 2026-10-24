"""Reviewed project Wiki pages tagged for a wider audience reach the shared roots."""
from __future__ import annotations

import os
from pathlib import Path

from argus.wiki.correct import retire_page
from argus.wiki.promote import promote_wiki_pages


def _page(title: str, description: str, body: str, *, audience: str | None = None, vertical: str = "") -> str:
    extra = f"audience: {audience}\n" if audience else ""
    extra += f"vertical: {vertical}\n" if vertical else ""
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

    assert promoted == {"research": ["notes.md"]}
    assert "New body." in target.read_text(encoding="utf-8")
    # The index already names the page; no second line is added.
    index = (shared / "_shared_verticals" / "research" / "INDEX.md").read_text(encoding="utf-8")
    assert index.count("](pages/notes.md)") == 1

    # An older project page never overwrites a newer shared copy.
    _write(wiki / "pages" / "notes.md", _page("Notes", "Stale", "Stale body.", audience="vertical"), 100)
    assert promote_wiki_pages(workspace, vertical="research", shared_root=shared) == {}
    assert "New body." in target.read_text(encoding="utf-8")


def test_a_retired_shared_copy_stays_retired_until_the_project_page_changes(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    source = _project_wiki(workspace) / "pages" / "env" / "safe-path.md"
    _write(source, _page("Safe path", "Set PYTHONPATH", "Body.", audience="vertical"), 1_790_000_000)
    assert promote_wiki_pages(workspace, vertical="software", shared_root=shared) == {"software": ["env/safe-path.md"]}
    library = shared / "_shared_verticals" / "software"
    retire_page(library, "env/safe-path.md", reason="The host no longer sets PYTHONSAFEPATH.", now=1_790_000_100)

    # Retired after the project page last changed: promotion does not bring it back.
    assert promote_wiki_pages(workspace, vertical="software", shared_root=shared) == {}
    assert not (library / "pages" / "env" / "safe-path.md").exists()

    # Changed at the source after the retirement, the page is shared again.
    _write(source, _page("Safe path", "Only under PYTHONSAFEPATH", "Body.", audience="vertical"), 1_790_000_200)
    assert promote_wiki_pages(workspace, vertical="software", shared_root=shared) == {"software": ["env/safe-path.md"]}
    assert "Only under PYTHONSAFEPATH" in (library / "pages" / "env" / "safe-path.md").read_text(encoding="utf-8")
    assert "](pages/env/safe-path.md)" in (library / "INDEX.md").read_text(encoding="utf-8")


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

    assert promoted == {"global": ["good.md"]}
    assert sorted(path.name for path in (shared / "_global" / "pages").iterdir()) == ["good.md"]


def test_vertical_pages_stay_home_when_no_vertical_is_known(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "pages" / "a.md", _page("A", "Vertical page", "Body.", audience="vertical"), 1_000)
    _write(wiki / "pages" / "b.md", _page("B", "Global page", "Body.", audience="global"), 1_000)

    assert promote_wiki_pages(workspace, vertical="", shared_root=shared) == {"global": ["b.md"]}
    assert promote_wiki_pages(workspace, vertical="../evil", shared_root=shared) == {}
    assert not (shared / "_shared_verticals").exists()


def test_a_page_names_the_vertical_it_belongs_to(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "pages" / "serving.md", _page("Serving", "How to serve", "Body.", audience="vertical", vertical="software"), 1_000)
    _write(wiki / "pages" / "eval.md", _page("Eval", "How to evaluate", "Body.", audience="vertical", vertical="no-such"), 1_000)

    promoted = promote_wiki_pages(workspace, vertical="research", shared_root=shared)

    assert promoted == {"software": ["serving.md"], "research": ["eval.md"]}
    assert (shared / "_shared_verticals" / "software" / "pages" / "serving.md").is_file()
    assert "[Serving](pages/serving.md)" in (shared / "_shared_verticals" / "software" / "INDEX.md").read_text(encoding="utf-8")
    assert (shared / "_shared_verticals" / "research" / "pages" / "eval.md").is_file()
    assert not (shared / "_shared_verticals" / "no-such").exists()


def test_a_promoted_page_takes_the_checks_it_links_to(tmp_path):
    workspace, shared = tmp_path / "ws", tmp_path / "home" / "wiki"
    wiki = _project_wiki(workspace)
    _write(wiki / "checks" / "max_tokens.py", "assert True\n", 1_000)
    _write(workspace / "repo" / "sampling_params.py", "max_tokens = 16\n", 1_000)
    _write(wiki / "pages" / "defaults.md", _page(
        "Defaults", "Which max_tokens takes effect",
        "16 is a placeholder ([source](../../../../repo/sampling_params.py)); "
        "rerun [the check](../checks/max_tokens.py).", audience="global"), 2_000)

    promote_wiki_pages(workspace, vertical="research", shared_root=shared)

    page = shared / "_global" / "pages" / "defaults.md"
    check = (page.parent / "../checks/max_tokens.py").resolve()
    assert check == (shared / "_global" / "checks" / "max_tokens.py").resolve()
    assert check.read_text(encoding="utf-8") == "assert True\n"
    assert not list((shared / "_global").rglob("sampling_params.py")), "cited material stays home"

    _write(wiki / "checks" / "max_tokens.py", "assert 16 != 8192\n", 3_000)
    promote_wiki_pages(workspace, vertical="research", shared_root=shared)
    assert check.read_text(encoding="utf-8") == "assert 16 != 8192\n", "a newer check travels on its own"
