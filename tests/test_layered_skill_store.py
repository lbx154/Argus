"""Tests for the two-layer skill store (project + global).

These cover the contract Phase 2 (p2-layered-skills) introduces:

* project shadows global by name in the merged matcher view
* writes default to project; explicit promote moves to global
* path-based dispatch routes load/update/writeback to the right layer
* find_relevant runs across the merged set and resolves matches in
  whichever layer they live in (the matcher is a pure-LLM op so we
  drive it with a canned MemoryBackend response)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.layered import (
    LAYER_GLOBAL,
    LAYER_PROJECT,
    LayeredSkillStore,
)
from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import Skill, SkillStore


def _make_skill(name: str, description: str = "", category: str = "x") -> Skill:
    return Skill(
        name=name,
        description=description or f"do {name}",
        category=category,
        content=f"## When to use\n- {category} tasks\n\n## How to solve\n- step 1\n",
        version=1,
        created_at="2026-05-03T00:00:00+00:00",
    )


def _write(store: SkillStore, name: str, **kw: str) -> Skill:
    s = _make_skill(name, **kw)
    store.save(s)
    return s


def _layered(tmp_path: Path, runner=None, matcher_model: str = "") -> LayeredSkillStore:
    return LayeredSkillStore(
        project_dir=tmp_path / "project_skills",
        global_dir=tmp_path / "global_skills",
        runner=runner,
        matcher_model=matcher_model,
    )


# --- merging / shadowing --------------------------------------------------

def test_list_summaries_unions_both_layers(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    _write(layered.project, "alpha")
    _write(layered.global_, "beta")
    summaries = layered.list_summaries()
    by_name = {s["name"]: s for s in summaries}
    assert by_name["alpha"]["layer"] == LAYER_PROJECT
    assert by_name["beta"]["layer"] == LAYER_GLOBAL


def test_project_shadows_global_when_names_collide(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    _write(layered.project, "shared", description="project version")
    _write(layered.global_, "shared", description="global version")
    summaries = layered.list_summaries()
    assert sum(1 for s in summaries if s["name"] == "shared") == 1
    only = next(s for s in summaries if s["name"] == "shared")
    assert only["layer"] == LAYER_PROJECT
    assert only["description"] == "project version"


def test_project_shadow_is_case_insensitive(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    _write(layered.project, "Bisect-Flake")
    _write(layered.global_, "bisect-flake")
    summaries = layered.list_summaries()
    assert sum(1 for s in summaries if s["name"].casefold() == "bisect-flake") == 1


def test_layer_summaries_returns_only_one_layer(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    _write(layered.project, "p1")
    _write(layered.global_, "g1")
    proj = layered.layer_summaries(LAYER_PROJECT)
    glob = layered.layer_summaries(LAYER_GLOBAL)
    assert [s["name"] for s in proj] == ["p1"]
    assert [s["name"] for s in glob] == ["g1"]


# --- layer dispatch -------------------------------------------------------

def test_layer_for_path_resolves_correct_layer(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    p = _write(layered.project, "in-project")
    g = _write(layered.global_, "in-global")
    assert layered.layer_for_path(p.path) == LAYER_PROJECT
    assert layered.layer_for_path(g.path) == LAYER_GLOBAL
    assert layered.layer_for_path(str(tmp_path / "nowhere.md")) is None


def test_layer_for_skill_defaults_to_project_when_path_empty(
    tmp_path: Path,
) -> None:
    layered = _layered(tmp_path)
    fresh = _make_skill("never-saved")
    assert fresh.path == ""
    assert layered.layer_for_skill(fresh) == LAYER_PROJECT


def test_load_dispatches_to_owning_layer(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    p = _write(layered.project, "p")
    g = _write(layered.global_, "g")
    assert layered.load(p.path).name == "p"
    assert layered.load(g.path).name == "g"


# --- writes ---------------------------------------------------------------

def test_save_distilled_lands_in_project_by_default(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    raw = (
        "NAME: greet\n"
        "DESCRIPTION: say hi\n"
        "CATEGORY: chat\n"
        "CONTENT:\n## When to use\n- greetings\n\n## How to solve\n- reply hi\n"
    )
    skill = layered.save_distilled(
        task_description="say hi to the user",
        raw_distill_output=raw,
    )
    assert skill is not None
    assert layered.layer_for_skill(skill) == LAYER_PROJECT


def test_save_dispatches_to_existing_layer(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    g = _write(layered.global_, "g-existing")
    g.content += "\n## Update\n- new note\n"
    layered.save(g)
    # Reload and confirm it's still in global, not duplicated to project.
    reloaded = layered.load(g.path)
    assert reloaded is not None
    assert layered.layer_for_skill(reloaded) == LAYER_GLOBAL
    assert "new note" in reloaded.content
    assert layered.layer_summaries(LAYER_PROJECT) == []


def test_update_global_skill_forks_project_shadow(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    g = _write(layered.global_, "tweak-me")
    updated = layered.update_skill(g, "## Replaced\n- new\n", "tweaked it")

    assert layered.layer_for_skill(updated) == LAYER_PROJECT
    assert updated.version == 2
    assert "Replaced" in updated.content
    assert "Replaced" not in layered.global_.load(g.path).content
    visible = next(row for row in layered.list_summaries() if row["name"] == "tweak-me")
    assert visible["layer"] == LAYER_PROJECT


def test_archive_refuses_to_mutate_shared_global_skill(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    global_skill = _write(layered.global_, "shared")

    assert layered.archive(global_skill) is None
    assert Path(global_skill.path).is_file()

    events: list[dict] = []
    counts = SkillRouter(skill_store=layered).apply_ops(
        [{"op": "archive", "name": "shared"}],
        task="project task",
        on_event=events.append,
    )
    assert counts["rejected"] == 1
    assert any("shared global layer" in event.get("text", "") for event in events)


# --- promotion ------------------------------------------------------------

def test_promote_to_global_moves_file_and_drops_project_copy(
    tmp_path: Path,
) -> None:
    layered = _layered(tmp_path)
    p = _write(layered.project, "promote-me")
    old_path = Path(p.path)
    promoted = layered.promote_to_global(p)
    assert layered.layer_for_skill(promoted) == LAYER_GLOBAL
    assert Path(promoted.path).exists()
    assert not old_path.exists()


def test_promote_to_global_can_keep_project_copy(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    p = _write(layered.project, "fork-me")
    old_path = Path(p.path)
    layered.promote_to_global(p, delete_project_copy=False)
    assert old_path.exists()
    # And the global copy is also present.
    glob = layered.layer_summaries(LAYER_GLOBAL)
    assert any(s["name"] == "fork-me" for s in glob)


def test_promote_to_global_rejects_global_skill(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    g = _write(layered.global_, "already-global")
    with pytest.raises(ValueError, match="not in project layer"):
        layered.promote_to_global(g)


def test_promote_to_global_rejects_name_collision(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    _write(layered.global_, "shared-name")
    p = _write(layered.project, "shared-name")
    with pytest.raises(ValueError, match="name already exists"):
        layered.promote_to_global(p)
    # Original global skill remains the sole copy.
    glob_summaries = layered.layer_summaries(LAYER_GLOBAL)
    assert sum(1 for s in glob_summaries if s["name"] == "shared-name") == 1


def test_import_global_into_project_creates_independent_copy(
    tmp_path: Path,
) -> None:
    layered = _layered(tmp_path)
    g = _write(layered.global_, "useful")
    forked = layered.import_global_skill_into_project(g)
    assert layered.layer_for_skill(forked) == LAYER_PROJECT
    # Edit the project fork; global must remain untouched.
    layered.update_skill(forked, "## project tweak\n- here\n", "fork tweak")
    reloaded_global = layered.load(g.path)
    assert "project tweak" not in reloaded_global.content


# --- matcher across the merged view --------------------------------------

def test_find_relevant_can_match_a_global_skill_when_project_empty(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "set-up-nginx", "fit": "high"}],
        }),
        input_tokens=12,
        cached_input_tokens=3,
        output_tokens=4,
    ))
    layered = _layered(tmp_path, runner=backend, matcher_model="m")
    _write(layered.global_, "set-up-nginx", description="configure nginx")
    matched, _ = layered.find_relevant("install nginx and serve files")
    assert matched is not None and len(matched) == 1
    assert matched[0].name == "set-up-nginx"
    assert layered.layer_for_skill(matched[0]) == LAYER_GLOBAL
    assert layered.role_for(matched[0]) == "general"
    assert layered.last_match_input_tokens == 12
    assert layered.last_match_cached_input_tokens == 3
    assert layered.last_match_output_tokens == 4
    assert layered.last_match_premium_requests == 0.0


def test_find_relevant_prefers_project_when_names_collide(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "shared", "fit": "high"}],
        }),
    ))
    layered = _layered(tmp_path, runner=backend, matcher_model="m")
    _write(layered.project, "shared", description="project flavor")
    _write(layered.global_, "shared", description="global flavor")
    matched, _ = layered.find_relevant("do the shared thing")
    assert matched is not None and len(matched) == 1
    assert layered.layer_for_skill(matched[0]) == LAYER_PROJECT
    assert "project flavor" in matched[0].description


def test_find_relevant_short_circuits_when_both_layers_empty(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    layered = _layered(tmp_path, runner=backend, matcher_model="m")
    matched, tokens = layered.find_relevant("anything")
    assert matched is None
    assert tokens == 0
    assert backend.history == []


def test_layer_summaries_rejects_unknown_layer(tmp_path: Path) -> None:
    layered = _layered(tmp_path)
    with pytest.raises(ValueError, match="unknown skill layer"):
        layered.layer_summaries("staging")
