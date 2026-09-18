"""What Argus knows about the operator stays private, comes first in recall, and reaches the roles.

The first answer-learning pass copied an operator's cap table into a global
survey page. Personal facts now go to ``<home>/operator/`` — a profile and
notes read only by Argus working for that operator — and the shared page
keeps the general finding.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from argus.core.paths import operator_memory_root
from argus.core.pipeline_state import write_pipeline_state
from argus.core.session import SessionMeta, write_session_meta
from argus.life.knowledge_recall import knowledge_recall_for_memory
from argus.life.memory import MemoryBundle
from argus.life.reflection import build_answer_prompt, build_reflection_prompt
from argus.webapi.server import create_app
from argus.wiki.context import render_operator_memory_block
from argus.wiki.journal import append_knowledge_event, read_knowledge_events

PROFILE = (
    "---\ntitle: The operator\ndescription: who they are and what they are building\n"
    "kind: profile\naudience: private\n---\n\n## Situation\nFounding a company; raising a seed round.\n"
)
NOTE = (
    "---\ntitle: Cap table as stated\ndescription: the split the operator described\n"
    "kind: note\naudience: private\nsource: chat/s-1\ncreated: 2026-09-18\n---\n\nCEO 65, CTO 30, CFO 5.\n"
)


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    root = operator_memory_root(home)
    (root / "pages").mkdir(parents=True)
    (root / "profile.md").write_text(PROFILE, encoding="utf-8")
    (root / "pages" / "cap-table.md").write_text(NOTE, encoding="utf-8")
    return home


def test_the_block_shows_the_profile_and_note_titles_and_forbids_copying(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    block = render_operator_memory_block(home)
    assert block.startswith("## What Argus knows about the operator (private)")
    assert "Founding a company" in block and "Cap table as stated" in block
    assert "never copy any of it into a shared page" in block
    assert render_operator_memory_block(tmp_path / "empty") == ""


def test_recall_reads_the_operator_first_under_the_private_scope(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_TRIAL", raising=False)
    workspace = home / "workspaces" / "ws"
    workspace.mkdir(parents=True)
    write_pipeline_state(workspace, {"vertical": "research"})
    recall = knowledge_recall_for_memory(MemoryBundle.for_cwd(workspace, global_root=home, fingerprint="p"))
    assert [root.kind for root in recall.roots[:2]] == ["about the operator", "notes about the operator"]
    assert all(root.scope == "private" for root in recall.roots[:2])
    text = recall.render_context("what did the operator say about the cap table and the seed round")
    assert "the split the operator described" in text and "about the operator" in text


def test_both_reflection_prompts_route_personal_facts_to_the_private_memory(tmp_path) -> None:
    root = tmp_path / "operator"
    answer = build_answer_prompt(
        project_id="s-1", vertical="", operator_text="q", reply="a" * 700, root=tmp_path / "wiki",
        existing=[], operator_root=root,
    )
    mission = build_reflection_prompt(
        project_id="s-1", vertical="research", mission_id="m", title="t", objective="o", acceptance="a",
        review_status="done", review_reason="", stop_reason="", host_round_log="", run_reality="",
        vertical_root=tmp_path / "v", project_wiki=None, skills_dir=tmp_path / "skills", existing_lessons=[],
        operator_root=root,
    )
    for prompt in (answer, mission):
        assert "goes to their private memory, not to a shared page" in prompt
        assert f"`{root}`" in prompt and "profile.md" in prompt and "kind: note" in prompt


def test_the_api_lists_the_private_library_first_with_the_profile_and_serves_its_pages(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(home, SessionMeta(id="demo", workdir=str(workspace)))
    append_knowledge_event(home, kind="learned", scope="private", vertical="", path="pages/cap-table.md",
                           title="Cap table as stated", source_project="demo", role="answer-learning", page_kind="note")
    client = TestClient(create_app(global_root=home, auth_token="t"))
    headers = {"Authorization": "Bearer t"}
    catalog = client.get("/api/wiki", params={"sid": "demo"}, headers=headers).json()
    assert catalog["scopes"][0] == "private"
    private = catalog["libraries"][0]
    assert private["scope"] == "private" and "Founding a company" in private["profile"]
    assert [page["title"] for page in private["pages"]] == ["Cap table as stated"]
    assert private["pages"][0]["kind"] == "note"
    page = client.get("/api/wiki/page", params={"scope": "private", "path": "pages/cap-table.md", "sid": "demo"}, headers=headers).json()
    assert "CEO 65" in page["content"]
    feed = client.get("/api/knowledge/feed", headers=headers).json()["events"]
    assert feed[0]["scope"] == "private"
    assert read_knowledge_events(home)[0]["scope"] == "private"
    # Nothing private is ever listed as shared.
    assert all(library["scope"] != "private" for library in catalog["libraries"][1:])
    assert json.dumps(catalog["libraries"][1:]).count("Cap table") == 0
