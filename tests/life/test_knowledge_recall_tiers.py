"""Recall reads the shared knowledge tiers, describes each page, and notes what it showed."""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import validate_event_envelope
from argus.core.paths import global_wiki_root, shared_vertical_wiki_root
from argus.core.pipeline_state import write_pipeline_state
from argus.life.knowledge_recall import (
    KnowledgeRoot,
    MarkdownKnowledgeRecall,
    knowledge_recall_for_memory,
    render_memory_recall,
)
from argus.life.memory import MemoryBundle

VERTICAL = "software"
OTHER_VERTICAL = "research"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def page(title: str, description: str, body: str, **front: str) -> str:
    extra = "".join(f"{key}: {value}\n" for key, value in front.items())
    return f"---\ntitle: {title}\ndescription: {description}\n{extra}---\n\n# {title}\n\n{body}\n"


def project_wiki(workspace: Path, sid: str, name: str, text: str) -> Path:
    wiki = workspace / ".autors" / sid / "wiki"
    write(wiki / "INDEX.md", "# Knowledge\n")
    return write(wiki / "pages" / name, text)


def workspace_with_vertical(root: Path, vertical: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write_pipeline_state(root, {"vertical": vertical})
    return root


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    home = tmp_path / "tenant"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_TRIAL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RECALL_SIBLING_WIKIS", raising=False)
    return home


@pytest.fixture
def journal(monkeypatch):
    """The knowledge journal, or a stand-in with the agreed file format when it is not installed yet."""
    try:
        import argus.wiki.journal  # noqa: F401
    except ImportError:
        module = types.ModuleType("argus.wiki.journal")

        def append_knowledge_event(global_root, **fields):
            record = {"ts": 0.0, **fields}
            path = Path(global_root) / "knowledge-journal.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        module.append_knowledge_event = append_knowledge_event
        monkeypatch.setitem(sys.modules, "argus.wiki.journal", module)

    def read(global_root: Path) -> list[dict]:
        path = Path(global_root) / "knowledge-journal.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    return read


def bundle(tenant: Path, workspace: Path) -> MemoryBundle:
    return MemoryBundle.for_cwd(workspace, global_root=tenant, fingerprint="project-a")


def root_kinds(recall: MarkdownKnowledgeRecall) -> list[str]:
    return [root.kind for root in recall.roots]


def test_roots_include_shared_tiers_principles_and_same_vertical_siblings_only(tenant):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    same = workspace_with_vertical(tenant / "workspaces" / "earlier-same", VERTICAL)
    other = workspace_with_vertical(tenant / "workspaces" / "earlier-other", OTHER_VERTICAL)
    undecided = tenant / "workspaces" / "undecided"
    project_wiki(same, "s-same", "notes.md", page("Same", "same vertical", "quartz same"))
    project_wiki(other, "s-other", "notes.md", page("Other", "other vertical", "quartz other"))
    project_wiki(undecided, "s-none", "notes.md", page("None", "no state", "quartz none"))
    project_wiki(workspace, "s-current", "notes.md", page("Current", "this project", "quartz current"))
    vertical_root = shared_vertical_wiki_root(VERTICAL, tenant)
    write(vertical_root / "pages" / "lessons" / "20260917-quartz.md", page("Lesson", "d", "quartz lesson"))
    write(vertical_root / "principles.md", "---\ntitle: Principles\ndescription: d\nkind: principles\n---\n\n1. Rule.\n")
    write(global_wiki_root(tenant) / "pages" / "quartz.md", page("Global", "d", "quartz global"))

    recall = knowledge_recall_for_memory(bundle(tenant, workspace))
    kinds = root_kinds(recall)
    by_kind = {root.kind: root for root in recall.roots}

    assert kinds[:4] == ["project Wiki", f"{VERTICAL} knowledge", "principles", "shared knowledge"]
    assert by_kind[f"{VERTICAL} knowledge"].path == vertical_root / "pages"
    assert by_kind[f"{VERTICAL} knowledge"].scope == "vertical"
    assert by_kind["principles"].path == vertical_root / "principles.md"
    assert by_kind["shared knowledge"].path == global_wiki_root(tenant) / "pages"
    assert by_kind["shared knowledge"].scope == "global"
    siblings = [root for root in recall.roots if root.kind.startswith("earlier project Wiki")]
    assert [root.kind for root in siblings] == ["earlier project Wiki (s-same)"]
    assert siblings[0].path == same / ".autors" / "s-same" / "wiki" / "pages"
    assert siblings[0].source == "s-same" and siblings[0].scope == "project"
    assert all(str(other) not in str(root.path) for root in recall.roots)
    assert all(str(undecided) not in str(root.path) for root in recall.roots)
    assert [root.kind for root in recall.roots if str(root.path).startswith(str(workspace))] == [
        "project Wiki", "native project Skill",
    ]
    context = recall.render_context("quartz")
    assert "earlier project Wiki (s-same)" in context and "quartz same" not in context
    assert str(other) not in context


@pytest.mark.parametrize("switch", [("ARGUS_SKILL_COPILOT_TRIAL", "1"), ("ARGUS_SKILL_RECALL_SIBLING_WIKIS", "0")])
def test_hosted_trial_or_switched_off_recall_never_reads_other_workspaces(tenant, monkeypatch, switch):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    same = workspace_with_vertical(tenant / "workspaces" / "earlier-same", VERTICAL)
    project_wiki(same, "s-same", "notes.md", page("Same", "d", "quartz same"))
    write(shared_vertical_wiki_root(VERTICAL, tenant) / "pages" / "shared.md", page("Shared", "d", "quartz shared"))
    monkeypatch.setenv(*switch)

    recall = knowledge_recall_for_memory(bundle(tenant, workspace))

    assert not any(root.kind.startswith("earlier project Wiki") for root in recall.roots)
    assert f"{VERTICAL} knowledge" in root_kinds(recall) and "shared knowledge" in root_kinds(recall)


def test_sibling_wikis_are_capped_newest_first_and_roots_stay_bounded(tenant):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    for number in range(12):
        sibling = workspace_with_vertical(tenant / "workspaces" / f"earlier-{number:02d}", VERTICAL)
        project_wiki(sibling, f"s-{number:02d}", "notes.md", page("P", "d", f"quartz {number}"))
        state = sibling / ".argus" / "PIPELINE_STATE.json"
        stamp = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp() + number * 3600
        import os

        os.utime(state, (stamp, stamp))

    recall = knowledge_recall_for_memory(bundle(tenant, workspace))
    siblings = [root.kind for root in recall.roots if root.kind.startswith("earlier project Wiki")]

    assert len(siblings) == 8
    assert siblings[0] == "earlier project Wiki (s-11)" and siblings[-1] == "earlier project Wiki (s-04)"
    assert len(recall.roots) <= 32


def test_document_budget_bounds_the_snapshot_across_every_tier(tmp_path):
    roots = []
    for tier in ("first", "second", "third"):
        for number in range(3):
            write(tmp_path / tier / f"{number}.md", f"quartz {tier} {number}")
        roots.append(KnowledgeRoot(tier, tmp_path / tier, tmp_path))
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", roots, max_documents=4)
    documents = recall._snapshot()
    assert len(documents) == 4
    assert {document.kind for document in documents} == {"first", "second"}


def test_rendering_describes_each_page_and_appends_the_vertical_principles(tenant):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    vertical_root = shared_vertical_wiki_root(VERTICAL, tenant)
    lesson = write(vertical_root / "pages" / "lessons" / "20260917-torch-search.md", page(
        "Search the torch docs first", "Read the installed torch version's docs before writing a kernel.",
        "quartz torch lesson body", kind="lesson", audience="vertical",
        source="s-fb4716b7/cf2c076f939e", created="2026-09-17", confidence="high",
    ))
    legacy = write(global_wiki_root(tenant) / "pages" / "legacy.md", "# Legacy quartz\n\nThe quartz mount needs a warm-up run.\n")
    principles = write(vertical_root / "principles.md", (
        "---\ntitle: Software principles\ndescription: Distilled rules\nkind: principles\n---\n\n"
        "# Principles\n\n1. Read the installed docs before writing code — evidence: [a](pages/lessons/a.md), [b](pages/lessons/b.md)\n\n"
        "## History\n- 2026-09-17 recompiled\n"
    ))

    context = bundle(tenant, workspace).render_recall_context("quartz")
    lines = context.splitlines()

    assert lines[0] == "### What Argus already knows (advisory)"
    assert "Current Wiki and Skill pointers" not in context
    lesson_line = next(line for line in lines if str(lesson) in line)
    assert lesson_line.startswith(f"- {VERTICAL} knowledge: `{lesson}` — Read the installed torch version's docs before writing a kernel. (lesson; s-fb4716b7/cf2c076f939e; 2026-09-17)")
    assert "[content " in lesson_line and "embedding similarity" not in lesson_line
    legacy_line = next(line for line in lines if str(legacy) in line)
    legacy_date = dt.date.fromtimestamp(legacy.stat().st_mtime).isoformat()
    assert f"— The quartz mount needs a warm-up run. (page; global; {legacy_date})" in legacy_line
    assert lines.index(lesson_line) < lines.index(legacy_line)
    assert lines[-1] == f"- principles: `{principles}` — 1. Read the installed docs before writing code — evidence: [a](pages/lessons/a.md), [b](pages/lessons/b.md)"
    assert "## History" not in context and "Distilled rules" not in lines[-1]


def test_principles_alone_are_shown_and_descriptions_stay_bounded(tmp_path):
    vertical_root = tmp_path / "wiki" / "_shared_verticals" / VERTICAL
    long_description = "quartz " * 60
    write(vertical_root / "pages" / "long.md", page("Long", long_description.strip(), "body"))
    principles = write(vertical_root / "principles.md", "---\ntitle: P\ndescription: d\nkind: principles\n---\n\n1. Keep it short.\n")
    roots = [
        KnowledgeRoot(f"{VERTICAL} knowledge", vertical_root / "pages", tmp_path, scope="vertical", vertical=VERTICAL, library=vertical_root),
        KnowledgeRoot("principles", vertical_root / "principles.md", tmp_path, scope="vertical", vertical=VERTICAL, library=vertical_root),
    ]
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", roots)

    result = recall.recall("quartz")
    (hit,) = result.hits
    description = hit.line.split(" — ", 1)[1].split(" (page;")[0]
    assert len(description) <= 160 and description.endswith("…")
    assert result.principles is not None and result.principles.document.relative_path == "principles.md"

    only_principles = recall.recall("unrelated topic")
    assert only_principles.hits == ()
    assert only_principles.principles is not None
    assert only_principles.text.splitlines()[-1] == f"- principles: `{principles}` — 1. Keep it short."
    for budget in (1, 40, 120, 400):
        bounded = recall.recall("quartz", max_chars=budget)
        assert len(bounded.text) <= budget
        assert all(hit.line + "\n" in bounded.text for hit in bounded.shown)


def test_matching_lesson_outranks_equal_page_and_one_lesson_is_always_shown(tmp_path):
    pages = tmp_path / "pages"
    for number in range(5):
        write(pages / f"p{number}.md", page(f"Page {number}", f"plain page {number}", "quartz sapphire measured"))
    lesson = write(pages / "lessons" / "l.md", page("Lesson", "a boundary learned", "quartz only", kind="lesson"))
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", [KnowledgeRoot("Wiki", pages, tmp_path)])

    result = recall.recall("quartz sapphire")
    kinds = [hit.document.meta.page_kind for hit in result.hits]
    assert len(result.hits) == 4 and kinds.count("page") == 3 and kinds[-1] == "lesson"
    assert result.hits[-1].document.path == lesson

    equal = write(pages / "equal.md", page("Equal", "plain page", "quartz only"))
    result = recall.recall("quartz")
    first_two = [hit.document.path for hit in result.hits[:2]]
    assert first_two[0] == lesson and equal in first_two


def test_recall_records_each_shown_page_in_the_journal_and_one_event_per_prompt(tenant, journal):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    vertical_root = shared_vertical_wiki_root(VERTICAL, tenant)
    write(vertical_root / "pages" / "lessons" / "20260917-torch-search.md", page(
        "Search the torch docs first", "d", "quartz torch", kind="lesson", source="s-fb4716b7/cf2c076f939e",
    ))
    write(vertical_root / "principles.md", "---\ntitle: P\ndescription: d\nkind: principles\n---\n\n1. Rule.\n")
    write(global_wiki_root(tenant) / "pages" / "g.md", page("Global quartz", "d", "quartz global"))
    project_wiki(workspace, "s-current", "own.md", page("Own quartz", "d", "quartz own"))
    memory = bundle(tenant, workspace)

    assert memory.render_recall_context("quartz", role="planner", mission_id="m-1")

    records = journal(tenant)
    assert records and all(record["kind"] == "recalled" for record in records)
    by_path = {record["path"]: record for record in records}
    lesson = by_path["pages/lessons/20260917-torch-search.md"]
    assert lesson["scope"] == "vertical" and lesson["vertical"] == VERTICAL
    assert lesson["title"] == "Search the torch docs first" and lesson["page_kind"] == "lesson"
    assert lesson["source_project"] == "s-fb4716b7/cf2c076f939e"
    assert lesson["role"] == "planner" and lesson["mission_id"] == "m-1"
    assert by_path["principles.md"]["page_kind"] == "principles"
    assert by_path["pages/g.md"]["scope"] == "global"
    assert by_path["pages/own.md"]["scope"] == "project"
    events = [json.loads(line) for line in (memory.project_root / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    recalled = [event for event in events if event.get("type") == "knowledge.recalled"]
    assert len(recalled) == 1
    event = recalled[0]
    assert "event_validation" not in event and validate_event_envelope(event).valid
    assert event["role"] == "planner" and event["mission_id"] == "m-1"
    assert event["objective_excerpt"] == "quartz"
    assert sorted(event["paths"]) == sorted(str(Path(p)) for p in event["paths"]) and len(event["paths"]) == len(records)
    assert event["scope_counts"] == {"global": 1, "project": 1, "vertical": 2}


def test_recall_records_default_to_the_engineer_and_a_failing_journal_never_breaks_the_prompt(tenant, journal, monkeypatch):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    project_wiki(workspace, "s-current", "own.md", page("Own quartz", "d", "quartz own"))
    memory = bundle(tenant, workspace)

    assert "Own quartz" not in memory.render_recall_context("granite")
    assert journal(tenant) == []
    assert memory.render_prelude(objective="quartz")
    assert {record["role"] for record in journal(tenant)} == {"engineer"}
    assert memory.render_prelude(objective="quartz", role="manager")
    assert "manager" in {record["role"] for record in journal(tenant)}

    def broken(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(sys.modules["argus.wiki.journal"], "append_knowledge_event", broken)
    scope = SimpleNamespace(project_root=memory.project_root, project_worktree=workspace, global_root=tenant,
                            render_failure_experience_context=lambda *_a, **_k: "")
    rendered = render_memory_recall(scope, "quartz", role="engineer", life_dir=memory.project_root)
    assert "Own quartz" in rendered or "own.md" in rendered


def test_nothing_is_recorded_when_recall_is_empty(tenant, journal):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    memory = bundle(tenant, workspace)
    assert memory.render_recall_context("quartz") == ""
    assert journal(tenant) == []
    assert not (memory.project_root / "events.jsonl").exists() or "knowledge" not in (memory.project_root / "events.jsonl").read_text(encoding="utf-8")
