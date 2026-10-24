from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life.knowledge_recall import (
    KnowledgeRoot,
    MarkdownKnowledgeRecall,
    knowledge_recall_for_memory,
)
from argus.life.memory import BacklogItem, MemoryBundle


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def rows(recall):
    with closing(sqlite3.connect(recall.index.path)) as connection:
        return list(connection.execute("SELECT id,revision,version,direct_terms,transfer_terms,vector FROM documents"))


def wiki(workspace: Path, text: str) -> Path:
    root = workspace / ".autors" / "project" / "wiki"
    write(root / "INDEX.md", "# Knowledge\n")
    return write(root / "pages" / "measurement.md", text)


def test_corrupt_index_rebuild_does_not_resurrect_removed_markdown(tmp_path):
    root = tmp_path / "skills"
    first = write(root / "first.md", "obsolete quartz")
    second = write(root / "second.md", "current sapphire")
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", [KnowledgeRoot("Skill", root, root)])
    recall.sync()
    first.unlink()
    recall.index.path.write_bytes(b"not a sqlite database")
    assert str(second) in recall.render_context("sapphire")
    assert len(rows(recall)) == 1
    assert "quartz" not in rows(recall)[0][4]
    assert not recall.render_context("quartz")


def test_bounds_and_scope_exclude_symlinked_hidden_retired_or_oversized_files(tmp_path):
    root = tmp_path / "scope"
    valid = write(root / "valid.md", "bounded quartz")
    foreign = write(tmp_path / "foreign" / "secret.md", "foreign granite")
    (root / "outside.md").symlink_to(foreign)
    (root / "linked-directory").symlink_to(foreign.parent, target_is_directory=True)
    for directory in ("_archive", "_retired", "_history", ".hidden"):
        write(root / directory / "old.md", "retired ruby")
    write(root / "large.md", "large emerald " * 1000)
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", [KnowledgeRoot("Skill", root, root)], max_file_bytes=100)
    assert str(valid) in recall.render_context("quartz")
    assert len(rows(recall)) == 1
    assert not recall.render_context("granite ruby emerald")
    moved = tmp_path / "original-scope"
    root.rename(moved)
    root.symlink_to(foreign.parent, target_is_directory=True)
    recall.sync()
    assert rows(recall) == []


def test_memory_planner_and_engineer_use_current_knowledge_with_shared_budget(tmp_path):
    from argus.life.supervisor._mission_execution_runtime import MissionExecutionRuntimeMixin
    from argus.life.supervisor._planner_rendering import PlannerRenderingMixin

    workspace = tmp_path / "workspace"
    page = wiki(workspace, "quartz measured constraint")
    memory = MemoryBundle.for_cwd(workspace, global_root=tmp_path / "tenant", fingerprint="project-a")
    item = BacklogItem.new(title="quartz", objective="quartz")

    class Host(PlannerRenderingMixin, MissionExecutionRuntimeMixin):
        def _render_campaign_tally(self):
            return ""

        def _render_backlog_item_metadata(self, _item):
            return ""

    host = Host()
    host.memory = memory
    host.config = SimpleNamespace(continuous_objective="quartz", runtime_context="")
    for context in (memory.render_prelude(objective="quartz"), host._render_journal_entries_for_planner([]),
                    host._build_mission_prelude(item)):
        assert str(page) in context
    write(page, "quartz revised measured constraint")
    assert "quartz measured constraint" not in memory.render_prelude(objective="quartz")
    for budget in (1, 20, 600, 6000):
        assert len(memory.render_recall_context("quartz", max_chars=budget)) <= budget
    page.unlink()
    assert str(page) not in memory.render_prelude(objective="quartz")
    assert str(page) not in host._render_journal_entries_for_planner([])
    assert str(page) not in host._build_mission_prelude(item)


def test_memory_roots_use_explicit_tenant_and_configured_skill_scopes(tmp_path, monkeypatch):
    from argus.skills.layered import LayeredSkillStore

    tenant = tmp_path / "tenant"
    memory = MemoryBundle.for_cwd(tmp_path / "workspace", global_root=tenant, fingerprint="project-a")
    shared = write(tenant / "skills" / "general.md", "quartz general method")
    vertical = write(tenant / "skills" / "_shared_verticals" / "chosen" / "scoped.md", "quartz scoped method")
    excluded = write(tenant / "skills" / "_shared_verticals" / "other" / "foreign.md", "quartz other namespace")
    sibling = write(tenant / "projects" / "project-b" / "skills" / "private.md", "quartz sibling private")
    monkeypatch.setenv("HOME", str(tmp_path / "ambient"))
    store = LayeredSkillStore(project_dir=memory.project_root / "skills", global_dir=tenant / "skills", vertical_dir=vertical.parent)
    recall = knowledge_recall_for_memory(memory, skill_store=store)
    context = recall.render_context("quartz")
    assert str(shared) in context and str(vertical) in context
    assert str(excluded) not in context and str(sibling) not in context


def test_mismatched_derived_score_ids_fall_back_to_current_source(tmp_path, monkeypatch):
    from argus.life.failure_experience_index import RecallScore

    page = write(tmp_path / "pages" / "current.md", "quartz measurement")
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", [KnowledgeRoot("Wiki", page.parent, tmp_path)])
    monkeypatch.setattr(recall.index, "scores", lambda *_args, **_kwargs: {"another-snapshot": RecallScore(5)})
    assert str(page) in recall.render_context("quartz")


def test_wiki_discovery_is_bounded_before_index_construction(tmp_path, monkeypatch):
    from argus.wiki import auto_hooks

    workspace = tmp_path / "workspace"
    autors = workspace / ".autors"
    for number in range(300):
        (autors / f"project-{number}").mkdir(parents=True)
    memory = MemoryBundle.for_cwd(workspace, global_root=tmp_path / "tenant", fingerprint="project-a")
    original_scandir = os.scandir
    inspected = []

    class Counted:
        def __enter__(self):
            self.iterator = original_scandir(autors)
            return self

        def __exit__(self, *_args):
            self.iterator.close()

        def __next__(self):
            inspected.append(1)
            assert len(inspected) <= 256
            return next(self.iterator)

    def forbidden(_root):
        raise AssertionError("unbounded Wiki discovery was used")

    monkeypatch.setattr(auto_hooks, "discover_wikis", forbidden)
    monkeypatch.setattr(os, "scandir", lambda path: Counted() if Path(path) == autors else original_scandir(path))
    recall = knowledge_recall_for_memory(memory)
    assert len(inspected) == 256
    assert len(recall.roots) <= 32


@pytest.mark.parametrize("verdict_source", ["reviewer", "engineer", ""])
def test_successful_settlement_retains_bounded_observation_without_inventing_causality(tmp_path, verdict_source):
    from argus.life.supervisor._mission_execution_helpers import _MissionRunState
    from argus.life.supervisor._mission_execution_settlement import (
        MissionExecutionSettlementMixin,
    )

    memory = MemoryBundle.for_cwd(tmp_path / "workspace", global_root=tmp_path / "tenant", fingerprint="project-a")
    item = BacklogItem.new(title="scoped quartz success", objective="test quartz under one condition")
    state = _MissionRunState(item=item, usage_attempt_id="attempt", status="completed", success=True,
                             stop_reason="bounded trial passed", outcome=SimpleNamespace(
                                 final_message="Observed the metric under the stated conditions.",
                                 final_review_source=verdict_source, final_review_reason="one scoped observation"))
    host = SimpleNamespace(memory=memory)
    MissionExecutionSettlementMixin._capture_failure_experience(host, state)
    MissionExecutionSettlementMixin._capture_failure_experience(host, state)
    (captured,) = memory.failure_experiences.recent()
    assert captured.revision == 1 and captured.status == "completed"
    assert captured.passed_assumptions == [] and captured.causes == []
    assert "not a general causal rule" in captured.claim_boundaries[0]
    assert (verdict_source or "unspecified") in captured.claim_boundaries[-1]
    assert "bounded trial passed" in memory.render_recall_context("quartz")
    assert "Prior mission experiences" in memory.render_recall_context("quartz")


def test_a_planning_cycle_recalls_once_and_as_the_planner():
    """Trial s-cc7ba48e 1c: the Planner's recall was recorded as the Engineer's;
    a cycle renders both the full journal and its delta, and must recall once."""
    from argus.life.supervisor._planner_rendering import PlannerRenderingMixin

    calls = []

    class Memory:
        def render_recall_context(self, objective, *, role="engineer", mission_id=""):
            calls.append((objective, role, mission_id))
            return "### recalled page"

    class Host(PlannerRenderingMixin):
        def _render_campaign_tally(self):
            return ""

    host = Host()
    host.memory = Memory()
    host.config = SimpleNamespace(continuous_objective="quartz")
    host._planner_session_journal_keys = frozenset()
    entry = SimpleNamespace(ts=1.0, kind="mission_complete", title="t", summary="s", extra={})

    recall = host._planner_recall_context()
    tail = host._render_journal_entries_for_planner([entry], recall)
    delta, _keys = host._render_journal_delta_for_planner([entry], recall)

    assert calls == [("quartz", "planner", "")]
    assert "### recalled page" in tail and "### recalled page" in delta
