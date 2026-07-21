"""Manager skill tidy-up: placement judge + write distilled skills back to the
argus SOURCE tree (builtin / vertical) and commit.

Covers the "janitor" path: after a mission the Manager reviews the runtime
library's distilled skills and writes the new ones (not already in source) into
``builtin_skills/`` (cross-domain) or ``verticals/<v>/skills/`` (domain), then
commits. All source-writing tests isolate ``builtin_skill_source_path`` /
``vertical_skill_source_path`` to a tmp dir so the real argus repo is untouched.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

import argus_skill.skills.builtins as builtins_mod
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.manager import skill_tidy, source_writeback
from argus_skill.manager.skill_review import (
    PlacementVerdict,
    classify_skill_placement,
    classify_skill_placements,
)
from argus_skill.skills.layered import (
    LAYER_VERTICAL,
    LayeredSkillStore,
    shared_vertical_skills_dir,
)
from argus_skill.skills.store import Skill, SkillStore, shared_skill_digest


def _runner(message: str) -> MemoryBackend:
    return MemoryBackend(default=CannedResponse(message=message))


def _skill(name: str) -> Skill:
    return Skill(
        name=name,
        description=f"do {name}",
        category="x",
        content="## When to use\n- x tasks\n\n## How to solve\n- step 1\n",
        version=1,
        created_at="2026-05-03T00:00:00+00:00",
    )


@pytest.fixture
def isolated_source(tmp_path, monkeypatch):
    """Point the argus source paths at a tmp git repo; return (root, builtin)."""
    root = tmp_path / "argus_skill"
    builtin = root / "builtin_skills"
    (builtin / "engineer").mkdir(parents=True)
    (builtin / "reviewer").mkdir(parents=True)
    monkeypatch.setattr(builtins_mod, "builtin_skill_source_path", lambda: builtin)
    monkeypatch.setattr(
        builtins_mod,
        "vertical_skill_source_path",
        lambda v: root / "verticals" / v / "skills",
    )
    # A real git repo so commit_to_source can actually commit.
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    # Auto-commit is opt-in (default OFF so a real mission never commits to the
    # operator's repo); these source-writing tests exercise the commit path.
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "1")
    return root, builtin


# --- classify_skill_placement: fail-soft + candidate guard ------------------


def test_placement_global() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"global","vertical":"","why":"general"}'),
    )
    assert v.placement == "global"


def test_placement_vertical_in_candidates() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"vertical","vertical":"quant","why":"factor"}'),
    )
    assert v.placement == "vertical" and v.vertical == "quant"


def test_placement_vertical_not_in_candidates_falls_to_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"vertical","vertical":"bogus","why":"x"}'),
    )
    assert v.placement == "stay"


def test_placement_no_runner_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"], runner=None,
    )
    assert v.placement == "stay"


def test_placement_unparseable_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner("not json"),
    )
    assert v.placement == "stay"


def test_batch_placement_classifies_multiple_skills_in_one_call() -> None:
    runner = MemoryBackend()
    runner.queue(
        "manager.skill_placement_batch",
        CannedResponse(message=(
            '{"placements":['
            '{"name":"general","placement":"global","vertical":"","why":"portable"},'
            '{"name":"factor","placement":"vertical","vertical":"quant","why":"domain"}'
            ']}'
        )),
    )

    verdicts = classify_skill_placements(
        skills=[
            {"name": "general", "task": "t", "content": "c"},
            {"name": "factor", "task": "t", "content": "c"},
        ],
        candidate_verticals=["quant"],
        runner=runner,
    )

    assert verdicts["general"].placement == "global"
    assert verdicts["factor"].placement == "vertical"
    assert verdicts["factor"].vertical == "quant"
    assert len(runner.history) == 1


def test_batch_placement_keeps_same_named_role_candidates_distinct() -> None:
    runner = MemoryBackend()
    runner.queue(
        "manager.skill_placement_batch",
        CannedResponse(message=(
            '{"placements":['
            '{"candidate_id":"engineer:id-1","placement":"global",'
            '"vertical":"","why":"portable"},'
            '{"candidate_id":"reviewer:id-2","placement":"vertical",'
            '"vertical":"quant","why":"review-only"}'
            ']}'
        )),
    )

    verdicts = classify_skill_placements(
        skills=[
            {
                "candidate_id": "engineer:id-1",
                "name": "shared name",
                "task": "t1",
                "content": "c1",
            },
            {
                "candidate_id": "reviewer:id-2",
                "name": "shared name",
                "task": "t2",
                "content": "c2",
            },
        ],
        candidate_verticals=["quant"],
        runner=runner,
    )

    assert verdicts["engineer:id-1"].placement == "global"
    assert verdicts["reviewer:id-2"].placement == "vertical"


# --- write_skill_to_source: lands in the right source dir -------------------

def test_write_skill_to_builtin(isolated_source) -> None:
    _root, builtin = isolated_source
    dest = skill_tidy.write_skill_to_source(_skill("gen"), "global", role="engineer")
    assert dest == builtin / "engineer" / "gen.md"
    assert dest.exists() and "do gen" in dest.read_text(encoding="utf-8")


def test_write_skill_to_vertical(isolated_source) -> None:
    root, _builtin = isolated_source
    dest = skill_tidy.write_skill_to_source(
        _skill("factor lens"), "vertical", vertical="quant", role="reviewer"
    )
    assert dest == root / "verticals" / "quant" / "skills" / "reviewer" / "factor-lens.md"
    assert dest.exists()


def test_write_skill_to_research_vertical(isolated_source) -> None:
    root, _builtin = isolated_source
    dest = skill_tidy.write_skill_to_source(
        _skill("figure repair"), "vertical", vertical="research", role="engineer"
    )
    assert dest == (
        root / "verticals" / "research" / "skills" / "engineer" / "figure-repair.md"
    )
    assert dest.exists()


def test_write_skill_invalid_vertical_returns_none(isolated_source) -> None:
    assert skill_tidy.write_skill_to_source(
        _skill("x"), "vertical", vertical="bogus"
    ) is None


# --- tidy_runtime_skills_to_source: route + skip factory --------------------


def test_tidy_routes_to_source_and_commits(isolated_source, tmp_path, monkeypatch) -> None:
    root, builtin = isolated_source
    # Only "factory-one" counts as already-in-source; the rest are new.
    monkeypatch.setattr(
        skill_tidy,
        "_collect_source_skill_names",
        lambda: {("general", "factory-one")},
    )

    runtime = SkillStore(tmp_path / "runtime")
    runtime.save(_skill("gen one"))
    runtime.save(_skill("factor one"))
    runtime.save(_skill("factory-one"))            # already in source → skip
    runtime.save(_skill("project-active"))

    def classify(*, content, task):
        if "factor" in task:
            return PlacementVerdict("vertical", "quant", "factor")
        return PlacementVerdict("global", "", "general")

    counts = skill_tidy.tidy_runtime_skills_to_source(runtime, classify)
    assert counts["to_builtin"] == 2
    assert counts["to_vertical"] == 1
    assert counts["errors"] == 0
    assert (builtin / "gen-one.md").exists()
    assert (root / "verticals" / "quant" / "skills" / "factor-one.md").exists()
    # factory source duplicate was not written; active project skill was routed.
    assert not (builtin / "factory-one.md").exists()
    assert (builtin / "project-active.md").exists()
    # a commit was produced with the two new files
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"],
        capture_output=True, text=True,
    ).stdout
    assert "[manager]" in log
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    assert status.strip() == ""  # everything committed


def test_tidy_uses_batch_classifier_instead_of_one_call_per_skill(
    isolated_source,
    tmp_path,
) -> None:
    _root, builtin = isolated_source
    runtime = SkillStore(tmp_path / "runtime-batch")
    runtime.save(_skill("one"))
    runtime.save(_skill("two"))
    batch_calls = []

    def classify(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("per-skill classifier should not run")

    def classify_batch(items):
        batch_calls.append(items)
        return {
            item["name"]: PlacementVerdict("global", "", "portable")
            for item in items
        }

    events = []
    counts = skill_tidy.tidy_runtime_skills_to_source(
        runtime,
        classify,
        classify_batch=classify_batch,
        on_event=events.append,
    )

    assert len(batch_calls) == 1
    assert len(batch_calls[0]) == 2
    assert counts["to_builtin"] == 2
    assert (builtin / "one.md").exists()
    assert (builtin / "two.md").exists()
    assert {event["name"] for event in events} == {"one", "two"}
    assert all(event["placement"] == "global" for event in events)
    assert all(event["path"].endswith(".md") for event in events)


def test_source_tidy_keeps_same_named_role_skills_distinct(
    isolated_source,
    tmp_path,
) -> None:
    _root, builtin = isolated_source
    runtime = SkillStore(tmp_path / "runtime-role")
    engineer = _skill("shared name")
    engineer.path = str(runtime.skills_dir / "engineer" / "shared-name.md")
    runtime.save(engineer)
    reviewer = _skill("shared name")
    reviewer.path = str(runtime.skills_dir / "reviewer" / "shared-name.md")
    runtime.save(reviewer)

    counts = skill_tidy.tidy_runtime_skills_to_source(
        runtime,
        lambda **kwargs: PlacementVerdict("global", "", "portable"),
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        },
    )

    assert counts["to_builtin"] == 2
    assert (builtin / "engineer" / "shared-name.md").is_file()
    assert (builtin / "reviewer" / "shared-name.md").is_file()


def test_tidy_chunks_large_skill_sets(monkeypatch, isolated_source, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_TIDY_BATCH_SIZE", "3")
    runtime = SkillStore(tmp_path / "runtime-chunks")
    for index in range(7):
        runtime.save(_skill(f"skill {index}"))
    sizes = []

    def classify_batch(items):
        sizes.append(len(items))
        return {
            item["name"]: PlacementVerdict("stay", "", "project-specific")
            for item in items
        }

    skill_tidy.tidy_runtime_skills_to_source(
        runtime,
        lambda **kwargs: PlacementVerdict("stay", "", "unused"),
        classify_batch=classify_batch,
    )

    assert sizes == [3, 3, 1]


def test_concurrent_tidy_does_not_create_numbered_source_duplicates(
    isolated_source,
    tmp_path,
    monkeypatch,
) -> None:
    _root, builtin = isolated_source
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "0")
    stores = [SkillStore(tmp_path / f"runtime-{index}") for index in range(2)]
    for store in stores:
        store.save(_skill("shared lesson"))
    barrier = threading.Barrier(2)
    results = []

    def classify_batch(items):
        barrier.wait()
        return {
            item["name"]: PlacementVerdict("global", "", "portable")
            for item in items
        }

    def worker(store):
        results.append(skill_tidy.tidy_runtime_skills_to_source(
            store,
            lambda **kwargs: PlacementVerdict("stay", "", "unused"),
            classify_batch=classify_batch,
        ))

    threads = [threading.Thread(target=worker, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result["to_builtin"] for result in results) == 1
    assert (builtin / "shared-lesson.md").exists()
    assert not (builtin / "shared-lesson-2.md").exists()


def test_commit_to_source_failsoft_non_git(tmp_path, monkeypatch) -> None:
    # builtin path under a NON-git dir → commit returns False, no raise.
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "1")  # opt in, so we reach the git logic
    builtin = tmp_path / "nogit" / "builtin_skills"
    builtin.mkdir(parents=True)
    monkeypatch.setattr(builtins_mod, "builtin_skill_source_path", lambda: builtin)
    f = builtin / "x.md"
    f.write_text("x", encoding="utf-8")
    assert source_writeback.commit_to_source([f], "msg") is False


def test_tidy_after_mission_reads_project_layer(tmp_path, monkeypatch) -> None:
    captured: dict[str, Path] = {}

    def _fake_tidy(
        runtime,
        classify,
        *,
        classify_batch=None,
        on_event=None,
    ):  # noqa: ARG001
        captured["skills_dir"] = runtime.skills_dir
        return {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}

    monkeypatch.setattr(skill_tidy, "tidy_runtime_skills_to_source", _fake_tidy)
    state = tmp_path / "state"

    skill_tidy.tidy_after_mission(
        tmp_path / "worktree",
        object(),
        project_state_dir=state,
    )

    assert captured["skills_dir"] == state / "skills"


def test_tidy_after_mission_failsoft_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    counts = skill_tidy.tidy_after_mission(tmp_path, runner=None)
    assert counts == {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}


def test_shared_propagation_is_immediate_and_cached(tmp_path) -> None:
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    learned = _skill("portable repair")
    learned.task_history = ["repair parser edge case"]
    runtime.save(learned)
    shared_root = tmp_path / "shared"
    calls = []

    def classify_batch(items):
        calls.append(items)
        return {
            items[0]["candidate_id"]: PlacementVerdict(
                "global",
                "",
                "cross-domain",
            ),
        }

    first = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=classify_batch,
    )
    second = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=classify_batch,
    )

    assert first["to_shared"] == 1
    assert second["cached"] == 1
    assert len(calls) == 1
    other_project = LayeredSkillStore(
        project_dir=tmp_path / "other-project",
        global_dir=shared_root,
    )
    propagated = next(
        row for row in other_project.list_summaries()
        if row["name"] == "portable repair"
    )
    assert propagated["layer"] == "global"
    shared_skill = other_project.load(propagated["path"])
    assert shared_skill.task_history == []
    assert shared_skill.skill_id == learned.skill_id


def test_vertical_shared_propagation_is_visible_only_to_that_vertical(tmp_path) -> None:
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    runtime.save(_skill("factor repair"))
    shared_root = tmp_path / "shared"

    result = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict(
                "vertical",
                "quant",
                "quant-only",
            )
            for item in items
        },
    )

    assert result["to_vertical_shared"] == 1
    vertical_dir = shared_vertical_skills_dir(shared_root, "quant")
    assert vertical_dir is not None
    quant_project = LayeredSkillStore(
        project_dir=tmp_path / "quant-project",
        global_dir=shared_root,
        vertical_dir=vertical_dir,
    )
    row = next(
        item for item in quant_project.list_summaries()
        if item["name"] == "factor repair"
    )
    assert row["layer"] == LAYER_VERTICAL
    software_project = LayeredSkillStore(
        project_dir=tmp_path / "software-project",
        global_dir=shared_root,
    )
    assert "factor repair" not in {
        item["name"] for item in software_project.list_summaries()
    }


def test_shared_update_preserves_incoming_version_and_rollback(tmp_path) -> None:
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    learned = _skill("portable repair")
    runtime.save(learned)
    shared_root = tmp_path / "shared"

    def globally(items):
        return {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        }

    skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=globally,
    )
    learned = runtime.load(learned.path)
    learned.content += "\n- revised mechanism\n"
    learned.version = 5
    runtime.save(learned)

    result = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=globally,
    )

    assert result["updated"] == 1
    shared = next(
        SkillStore(shared_root).load(str(row["path"]))
        for row in SkillStore(shared_root).list_summaries()
        if row["name"] == learned.name
    )
    assert shared.version == 5
    history = shared_root / "_history" / shared.skill_id / "v1.md"
    assert history.is_file()


def test_shared_update_preserves_higher_version_when_content_is_unchanged(
    tmp_path,
) -> None:
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    learned = _skill("portable repair")
    runtime.save(learned)
    shared_root = tmp_path / "shared"
    classify = lambda items: {
        item["candidate_id"]: PlacementVerdict("global", "", "portable")
        for item in items
    }
    skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=classify,
    )
    learned = runtime.load(learned.path)
    learned.version = 3
    runtime.save(learned)

    result = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=classify,
    )

    assert result["updated"] == 1
    shared = next(
        SkillStore(shared_root).load(str(row["path"]))
        for row in SkillStore(shared_root).list_summaries()
        if row["name"] == learned.name
    )
    assert shared.version == 3


def test_reclassification_retires_stale_global_copy(tmp_path) -> None:
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    learned = _skill("portable repair")
    runtime.save(learned)
    shared_root = tmp_path / "shared"
    ledger = state / "skill-propagation.json"

    skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=ledger,
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        },
    )
    learned = runtime.load(learned.path)
    global_path = Path(
        next(
            row["path"] for row in SkillStore(shared_root).list_summaries()
            if row["name"] == learned.name
        )
    )
    learned.content += "\n- quant-specific evidence\n"
    learned.version = 2
    runtime.save(learned)

    result = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=ledger,
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict(
                "vertical",
                "quant",
                "quant-specific",
            )
            for item in items
        },
    )

    assert result["to_vertical_shared"] == 1
    assert not global_path.exists()
    assert any((shared_root / "_archive").glob("portable-repair.*.md"))
    vertical_dir = shared_vertical_skills_dir(shared_root, "quant")
    assert vertical_dir is not None
    assert any(
        row["name"] == learned.name
        for row in SkillStore(vertical_dir).list_summaries()
    )


def test_idless_direct_skill_gets_stable_identity_before_sharing(tmp_path) -> None:
    state = tmp_path / "project-state"
    runtime_dir = state / "skills"
    runtime_dir.mkdir(parents=True)
    direct = _skill("direct reviewer lesson")
    (runtime_dir / "direct-reviewer-lesson.md").write_text(
        direct.render(),
        encoding="utf-8",
    )
    runtime = SkillStore(runtime_dir)
    shared_root = tmp_path / "shared"

    def globally(items):
        return {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        }

    first = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=globally,
    )
    project_skill = runtime.load(str(runtime_dir / "direct-reviewer-lesson.md"))
    shared_skill = next(
        SkillStore(shared_root).load(str(row["path"]))
        for row in SkillStore(shared_root).list_summaries()
        if row["name"] == project_skill.name
    )

    assert first["to_shared"] == 1
    assert project_skill.skill_id
    assert shared_skill.skill_id == project_skill.skill_id
    project_skill.content += "\n- revised\n"
    project_skill.version = 2
    runtime.save(project_skill)
    second = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=globally,
    )
    assert second["updated"] == 1


def test_stale_project_cannot_resurrect_newer_shared_version(tmp_path) -> None:
    shared_root = tmp_path / "shared"
    vertical_dir = shared_vertical_skills_dir(shared_root, "quant")
    assert vertical_dir is not None
    canonical_store = SkillStore(vertical_dir)
    base = _skill("portable repair")
    base.skill_id = "stable-id"
    base_digest = shared_skill_digest(base)
    canonical = _skill("portable repair")
    canonical.version = 2
    canonical.skill_id = "stable-id"
    canonical.content += "\n- canonical v2\n"
    canonical_store.save(canonical)

    stale_state = tmp_path / "stale-state"
    stale_store = SkillStore(stale_state / "skills")
    stale = _skill("portable repair")
    stale.version = 3
    stale.skill_id = "stable-id"
    stale.shared_base_digest = base_digest
    stale.shared_base_version = 1
    stale.content += "\n- stale fork edits\n"
    stale_store.save(stale)

    result = skill_tidy.propagate_runtime_skills_to_shared(
        stale_store,
        shared_root=shared_root,
        ledger_path=stale_state / "skill-propagation.json",
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        },
    )

    assert result["stayed"] == 1
    assert not any(
        row["name"] == stale.name
        for row in SkillStore(shared_root).list_summaries()
    )
    assert any(
        row["name"] == canonical.name and row["version"] == 2
        for row in canonical_store.list_summaries()
    )


def test_failed_rename_conflict_keeps_prior_shared_copy(tmp_path) -> None:
    shared_root = tmp_path / "shared"
    shared_store = SkillStore(shared_root)
    prior = _skill("old name")
    prior.skill_id = "moving-id"
    shared_store.save(prior)
    collision = _skill("new name")
    collision.skill_id = "other-id"
    shared_store.save(collision)
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    renamed = _skill("new name")
    renamed.skill_id = "moving-id"
    renamed.version = 2
    runtime.save(renamed)

    result = skill_tidy.propagate_runtime_skills_to_shared(
        runtime,
        shared_root=shared_root,
        ledger_path=state / "skill-propagation.json",
        classify_batch=lambda items: {
            item["candidate_id"]: PlacementVerdict("global", "", "portable")
            for item in items
        },
    )

    assert result["stayed"] == 1
    assert Path(prior.path).is_file()
    assert Path(collision.path).is_file()


def test_propagation_manager_session_stays_in_project_state(tmp_path) -> None:
    project = tmp_path / "worktree"
    project.mkdir()
    state = tmp_path / "project-state"
    runtime = SkillStore(state / "skills")
    runtime.save(_skill("portable repair"))
    shared_root = tmp_path / "shared"
    runner = MemoryBackend(default=CannedResponse(message=json.dumps({
        "placements": [{
            "candidate_id": next(iter(runtime.list_summaries()))["skill_id"],
            "placement": "global",
            "vertical": "",
            "why": "portable",
        }],
    })))

    result = skill_tidy.propagate_after_mission(
        project,
        runner,
        project_state_dir=state,
        shared_root=shared_root,
    )

    assert result["to_shared"] == 1
    assert not (project / ".manager_session.lock").exists()
    assert (state / ".manager_session.lock").exists()
