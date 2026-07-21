"""Manager placement and runtime-only cross-project Skill propagation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.manager import skill_tidy
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
