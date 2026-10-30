"""Recall reads the shared knowledge tiers, describes each page, and notes what it showed."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.paths import global_wiki_root, shared_vertical_wiki_root
from argus.core.pipeline_state import write_pipeline_state
from argus.life.knowledge_recall import (
    KnowledgeRoot,
    MarkdownKnowledgeRecall,
    knowledge_read_dirs,
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


def earlier_project(tenant: Path, sid: str, vertical: str, workspace: Path) -> Path:
    """A project as the host keeps it: the Manager's state under ``projects/<sid>``,
    the work in the workspace its session names (anywhere on disk)."""
    state = tenant / "projects" / sid
    workspace.mkdir(parents=True, exist_ok=True)
    write(state / "session.json", json.dumps({"id": sid, "workdir": str(workspace)}))
    if vertical:
        write_pipeline_state(state, {"vertical": vertical})
    return state


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    home = tmp_path / "tenant"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.delenv("ARGUS_SKILL_LEARNING", raising=False)
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


def test_what_a_learning_project_kept_is_recalled_in_every_vertical(tenant, tmp_path):
    learned = tmp_path / "work" / "learn"
    earlier_project(tenant, "s-learned", "learning", learned)
    project_wiki(learned, "learn", "engine-scheduler.md",
                 page("Engine scheduler", "how the engine schedules prefill", "quartz scheduler prefill"))
    write(learned / ".agents" / "skills" / "dependency-closed-check" / "SKILL.md",
          "---\nname: dependency-closed-check\ndescription: validate quartz modules\n---\n\nquartz\n")
    write(shared_vertical_wiki_root("learning", tenant) / "pages" / "lessons" / "20261020-narrow.md",
          page("Narrow the check", "narrow quartz checks", "quartz lesson", kind="lesson"))
    write(shared_vertical_wiki_root("learning", tenant) / "principles.md",
          "---\ntitle: P\ndescription: d\nkind: principles\n---\n\n1. Learning rule.\n")

    for sid, vertical in (("s-software", VERTICAL), ("s-undecided", "")):
        workspace = tmp_path / "work" / sid
        earlier_project(tenant, sid, vertical, workspace)
        memory = MemoryBundle.for_cwd(workspace, global_root=tenant, fingerprint=sid)

        result = knowledge_recall_for_memory(memory).recall("quartz scheduler")

        shown = {hit.document.path for hit in result.hits}
        assert learned / ".autors" / "learn" / "wiki" / "pages" / "engine-scheduler.md" in shown, vertical
        assert any(hit.document.vertical == "learning" and hit.document.meta.page_kind == "lesson"
                   for hit in result.hits)
        # Another vertical's principles govern its own missions, not this one.
        assert "Learning rule" not in result.text


def test_a_role_is_given_the_directories_recall_points_at_outside_its_workspace(tenant, tmp_path):
    workspace = tmp_path / "work" / "current"
    state = earlier_project(tenant, "s-current", VERTICAL, workspace)
    project_wiki(workspace, "current", "own.md", page("Own", "d", "quartz"))
    write(workspace / ".agents" / "skills" / "own" / "SKILL.md", "---\nname: own\ndescription: d\n---\n")
    earlier = tmp_path / "work" / "earlier"
    earlier_state = earlier_project(tenant, "s-earlier", OTHER_VERTICAL, earlier)
    project_wiki(earlier, "earlier", "engine.md", page("Engine", "d", "quartz"))
    write(earlier / ".agents" / "skills" / "check" / "SKILL.md", "---\nname: check\ndescription: d\n---\n")
    write(earlier_state / "skills" / "engineer" / "old.md", "---\nname: old\ndescription: d\n---\n")
    write(tenant / "skills" / "_shared_verticals" / VERTICAL / "engineer" / "shared.md", "shared\n")
    write(shared_vertical_wiki_root(OTHER_VERTICAL, tenant) / "pages" / "lesson.md", page("L", "d", "quartz"))
    write(tenant / "operator" / "profile.md", "---\ntitle: Me\ndescription: d\n---\n")

    dirs = knowledge_read_dirs(state, tenant, workspace)

    assert set(dirs) == {
        str(tenant / "skills"),
        str(shared_vertical_wiki_root(OTHER_VERTICAL, tenant)),
        str(earlier / ".autors" / "earlier" / "wiki"),
        str(earlier / ".agents" / "skills"),
        str(earlier_state / "skills" / "engineer"),
    }, "the operator's private memory is recalled, never handed to a backend"
    assert knowledge_read_dirs(None, tenant, workspace) == []


def test_an_earlier_page_s_sources_resolve_and_only_their_directories_are_readable(tenant, tmp_path):
    """Trial s-30b9eaed: a recalled page named ``repos/DeepInfer`` of an earlier
    workspace the backend could not read, so the agents fetched it from GitHub."""
    workspace = tmp_path / "work" / "current"
    state = earlier_project(tenant, "s-current", VERTICAL, workspace)
    earlier = tmp_path / "work" / "earlier"
    earlier_project(tenant, "s-earlier", VERTICAL, earlier)
    write(earlier / "repos" / "engine" / "core.py", "pass\n")
    write(earlier / "private" / "notes.py", "pass\n")
    write(earlier / ".cache" / "blob.py", "pass\n")
    project_wiki(earlier, "earlier", "engine.md", page(
        "Engine", "how the engine schedules",
        "quartz engine in `repos/engine`: [core](../../../../repos/engine/core.py), "
        "[cache](../../../../.cache/blob.py)",
    ))

    dirs = knowledge_read_dirs(state, tenant, workspace)
    context = knowledge_recall_for_memory(
        MemoryBundle.for_cwd(workspace, global_root=tenant, fingerprint=state.name),
    ).render_context("quartz engine")

    assert str(earlier / "repos") in dirs
    assert not {str(earlier), str(earlier / "private"), str(earlier / ".cache")} & set(dirs)
    assert f"workspace `{earlier}`" in context


def test_a_sandboxed_engineer_is_given_the_directories_recall_points_at(tmp_path):
    from argus.core.models import RunnerResult
    from argus.loop import SkillLoop, SkillLoopConfig

    class Capture:
        options = None

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            Capture.options = options
            return RunnerResult(exit_code=0, agent_messages=["done"])

    loop = SkillLoop(skills_dir=tmp_path / "skills", engineer_runner=Capture(),
                     config=SkillLoopConfig(add_dirs=["/elsewhere/wiki"]))
    loop.supervised._run_engineer(prompt="work", workdir=tmp_path, run_label="engineer-r1")

    assert Capture.options.add_dirs[0] == "/elsewhere/wiki"


@pytest.mark.parametrize("switch", ["policy", "learning"])
def test_disabled_sibling_recall_never_reads_other_workspaces(tenant, tmp_path, monkeypatch, switch):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    same = tmp_path / "work" / "earlier-same"
    earlier_project(tenant, "s-same", VERTICAL, same)
    project_wiki(same, "same", "notes.md", page("Same", "d", "quartz same"))
    write(shared_vertical_wiki_root(VERTICAL, tenant) / "pages" / "shared.md", page("Shared", "d", "quartz shared"))
    if switch == "policy":
        from argus.core.hosting import NO_HOSTING
        monkeypatch.setattr(NO_HOSTING, "sibling_libraries_enabled", lambda: False)
    else:
        monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")

    recall = knowledge_recall_for_memory(bundle(tenant, workspace))

    assert not any(root.kind.startswith("earlier project") for root in recall.roots)
    assert f"{VERTICAL} knowledge" in root_kinds(recall) and "shared knowledge" in root_kinds(recall)


def test_earlier_projects_are_capped_newest_first_and_roots_stay_bounded(tenant, tmp_path):
    workspace = workspace_with_vertical(tenant / "workspaces" / "current", VERTICAL)
    for number in range(16):
        sibling = tmp_path / "work" / f"earlier-{number:02d}"
        state = earlier_project(tenant, f"s-{number:02d}", VERTICAL, sibling)
        if number % 2:
            project_wiki(sibling, f"w-{number:02d}", "notes.md", page("P", "d", f"quartz {number}"))
            write(sibling / ".agents" / "skills" / "check" / "SKILL.md", "---\nname: check\ndescription: d\n---\n")
        stamp = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp() + number * 3600
        os.utime(state, (stamp, stamp))

    recall = knowledge_recall_for_memory(bundle(tenant, workspace))
    siblings = [root.source for root in recall.roots if root.kind.startswith("earlier project")]

    # Eight projects that kept something, each with its Wiki and its Skills;
    # a project that kept nothing does not use up a place.
    assert list(dict.fromkeys(siblings)) == [f"s-{number:02d}" for number in range(15, 0, -2)]
    assert len(siblings) == 16
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
    assert "Code listed" not in context
    lesson_line = next(line for line in lines if str(lesson) in line)
    assert lesson_line.startswith(f"- {VERTICAL} knowledge: `{lesson}` — Read the installed torch version's docs before writing a kernel. (lesson; s-fb4716b7/cf2c076f939e; 2026-09-17)")
    assert lesson_line.endswith("2026-09-17)"), "a lexical hit carries no channel tag"
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


def test_a_page_names_the_code_it_carries_and_not_the_material_it_cites(tmp_path):
    skills = tmp_path / "skills"
    write(skills / "cpu-check" / "SKILL.md",
          "---\nname: cpu-check\ndescription: quartz check\n---\n\nRun `python check.py <module>`.\n")
    write(skills / "cpu-check" / "check.py", "print('ok')\n")
    write(skills / "cpu-check" / "templates" / "case.rs", "fn main() {}\n")
    wiki = tmp_path / "wiki"
    write(wiki / "checks" / "default_max_tokens.py", "assert True\n")
    write(tmp_path / "repo" / "sampling_params.py", "max_tokens = 16\n")
    write(wiki / "pages" / "defaults.md", page(
        "Defaults", "quartz defaults",
        "A placeholder in [the source](../../repo/sampling_params.py); "
        "rerun [the check](../checks/default_max_tokens.py) on another version.",
    ))
    recall = MarkdownKnowledgeRecall(tmp_path / "index.sqlite3", [
        KnowledgeRoot("native project Skill", skills, tmp_path),
        KnowledgeRoot("project Wiki", wiki / "pages", tmp_path, library=wiki),
    ])

    lines = recall.recall("quartz").text.splitlines()

    assert lines[1].endswith(
        "Code listed with a page is relative to it; the page says how to run it, and "
        "rerunning it shows whether the page still holds.")
    assert next(line for line in lines if "SKILL.md" in line).endswith(
        "code: `check.py`, `templates/case.rs`")
    assert next(line for line in lines if "defaults.md" in line).endswith(
        "code: `../checks/default_max_tokens.py`")
