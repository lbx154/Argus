"""Manager skill tidy-up: the placement judge + project-layer routing.

Covers the "janitor" path: after a mission the Manager reviews the project
layer's distilled skills and routes each to global / its vertical / stay.
"""
from __future__ import annotations

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.manager._core import Manager
from argus_skill.manager.skill_review import (
    PlacementVerdict,
    classify_skill_placement,
)
from argus_skill.manager.skill_tidy import tidy_after_mission
from argus_skill.skills.layered import LayeredSkillStore
from argus_skill.skills.store import Skill


def _runner(message: str) -> MemoryBackend:
    return MemoryBackend(default=CannedResponse(message=message))


def _skill(name: str, *, provisional: bool = False) -> Skill:
    return Skill(
        name=name,
        description=f"do {name}",
        category="x",
        content="## When to use\n- x tasks\n\n## How to solve\n- step 1\n",
        version=1,
        created_at="2026-05-03T00:00:00+00:00",
        provisional=provisional,
    )


def _layered3(tmp_path):
    return LayeredSkillStore(
        project_dir=tmp_path / "p",
        vertical_dir=tmp_path / "v",
        global_dir=tmp_path / "g",
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
    assert v.placement == "vertical"
    assert v.vertical == "quant"


def test_placement_vertical_not_in_candidates_falls_to_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner('{"placement":"vertical","vertical":"bogus","why":"x"}'),
    )
    assert v.placement == "stay"  # never mis-file into an unoffered vertical


def test_placement_no_runner_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"], runner=None,
    )
    assert v.placement == "stay"


def test_placement_unparseable_is_stay() -> None:
    v = classify_skill_placement(
        content="c", task="t", candidate_verticals=["quant"],
        runner=_runner("not json at all"),
    )
    assert v.placement == "stay"


# --- tidy_project_skills: routing + provisional skip ------------------------


def test_tidy_routes_global_and_vertical_skips_provisional(
    tmp_path, monkeypatch
) -> None:
    layered = _layered3(tmp_path)
    layered.project.save(_skill("gen"))
    layered.project.save(_skill("dom"))
    layered.project.save(_skill("prov", provisional=True))  # must be skipped

    def fake_classify(self, *, content, task):
        if "gen" in task:
            return PlacementVerdict("global", "", "general")
        return PlacementVerdict("vertical", "quant", "factor")

    monkeypatch.setattr(Manager, "classify_skill_placement", fake_classify)
    mgr = Manager(tmp_path, runner=None)

    counts = mgr.tidy_project_skills(layered, active_vertical="quant")
    assert counts["promoted_global"] == 1
    assert counts["promoted_vertical"] == 1
    assert counts["errors"] == 0
    # provisional skill was skipped (not promoted, not counted as stayed)
    remaining = {s["name"] for s in layered.project.list_summaries()}
    assert remaining == {"prov"}


def test_tidy_skips_vertical_mismatch(tmp_path, monkeypatch) -> None:
    layered = _layered3(tmp_path)
    layered.project.save(_skill("dom"))

    monkeypatch.setattr(
        Manager,
        "classify_skill_placement",
        lambda self, *, content, task: PlacementVerdict("vertical", "quant", "v"),
    )
    mgr = Manager(tmp_path, runner=None)

    # active vertical is speedrun but the judge says quant -> conservative stay.
    counts = mgr.tidy_project_skills(layered, active_vertical="speedrun")
    assert counts["promoted_vertical"] == 0
    assert counts["stayed"] == 1


# --- tidy_after_mission: fail-soft end-to-end -------------------------------


def test_tidy_after_mission_failsoft_empty(tmp_path, monkeypatch) -> None:
    # Isolate the runtime home so the test never touches the real library.
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    counts = tidy_after_mission(tmp_path, runner=None)
    assert counts == {
        "promoted_global": 0,
        "promoted_vertical": 0,
        "stayed": 0,
        "errors": 0,
    }
