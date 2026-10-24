"""The Wiki prompt block names the shared knowledge roots a project reads."""
from __future__ import annotations

from argus.wiki.context import render_knowledge_wiki_block, shared_knowledge_roots


def test_shared_knowledge_is_available_without_a_project_wiki(tmp_path):
    shared = tmp_path / "shared" / "pages"
    shared.mkdir(parents=True)
    block = render_knowledge_wiki_block(tmp_path, role="Engineer", shared_roots=[shared.parent])
    assert f"`{shared.resolve()}`" in block
    assert "Search and read the Wiki yourself" in block
    # Trial 2: "直接做" opened `wiki/_global/INDEX.md`, which a shared Wiki never has.
    assert "INDEX.md" not in block and "Wiki directories" not in block
    assert render_knowledge_wiki_block(tmp_path, role="Engineer") == ""


def test_shared_roots_survive_an_unreadable_project_state(tmp_path):
    workspace, home = tmp_path / "ws", tmp_path / "home"
    state = workspace / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")
    (home / "wiki" / "_global" / "pages").mkdir(parents=True)
    assert shared_knowledge_roots(workspace, global_root=home) == [home / "wiki" / "_global"]
