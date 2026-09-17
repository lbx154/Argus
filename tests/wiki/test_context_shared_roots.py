"""The Wiki prompt block names the shared knowledge roots a project reads."""
from __future__ import annotations

from pathlib import Path

from argus.wiki.context import render_knowledge_wiki_block, shared_knowledge_roots

AUDIENCE_SENTENCE = (
    "A page useful beyond this project carries `audience: vertical` (or `audience: global`) "
    "in its front matter; after a passing review the host copies it into the shared knowledge "
    "of this vertical, which later projects read."
)


def _project_wiki(workspace: Path) -> Path:
    root = workspace / ".autors" / "proj" / "wiki"
    (root / "pages").mkdir(parents=True)
    (root / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    return root


def _decide_vertical(workspace: Path, vertical: str) -> None:
    state = workspace / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(f'{{"vertical": "{vertical}"}}', encoding="utf-8")


def test_block_without_shared_roots_is_unchanged(tmp_path):
    _project_wiki(tmp_path)
    block = render_knowledge_wiki_block(tmp_path, role="Engineer")
    assert block.startswith("## Shared project Wiki\nRole: Engineer\n")
    assert "Shared knowledge" not in block
    assert "audience:" not in block


def test_block_lists_shared_roots_and_the_audience_sentence(tmp_path):
    _project_wiki(tmp_path)
    vertical_root = tmp_path / "home" / "wiki" / "_shared_verticals" / "research"
    global_root = tmp_path / "home" / "wiki" / "_global"
    block = render_knowledge_wiki_block(
        tmp_path, role="Manager", shared_roots=[vertical_root, global_root]
    )
    assert "Shared knowledge (read before deciding; written by the host after review):\n" in block
    assert f"- `{vertical_root.resolve()}`\n- `{global_root.resolve()}`" in block
    assert AUDIENCE_SENTENCE in block
    assert block.index("Wiki directories:") < block.index("Shared knowledge")


def test_block_stays_empty_without_a_project_wiki(tmp_path):
    assert render_knowledge_wiki_block(tmp_path, role="Engineer", shared_roots=[tmp_path]) == ""


def test_shared_roots_follow_the_decided_vertical_and_need_pages(tmp_path):
    workspace, home = tmp_path / "ws", tmp_path / "home"
    workspace.mkdir()
    research = home / "wiki" / "_shared_verticals" / "research"
    shared_global = home / "wiki" / "_global"

    assert shared_knowledge_roots(workspace, global_root=home) == []

    (shared_global / "pages").mkdir(parents=True)
    assert shared_knowledge_roots(workspace, global_root=home) == [shared_global]

    _decide_vertical(workspace, "research")
    # The vertical directory exists but has no pages yet: not a root.
    research.mkdir(parents=True)
    assert shared_knowledge_roots(workspace, global_root=home) == [shared_global]

    (research / "pages").mkdir()
    assert shared_knowledge_roots(workspace, global_root=home) == [research, shared_global]


def test_shared_roots_survive_an_unreadable_project_state(tmp_path):
    workspace, home = tmp_path / "ws", tmp_path / "home"
    state = workspace / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")
    (home / "wiki" / "_global" / "pages").mkdir(parents=True)
    assert shared_knowledge_roots(workspace, global_root=home) == [home / "wiki" / "_global"]
