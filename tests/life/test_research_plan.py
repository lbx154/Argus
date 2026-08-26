"""Living research-plan persistence and mission projection."""

from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.memory import BacklogItem
from argus_skill.life.research_plan import (
    RESEARCH_PLAN_FILENAME,
    RESEARCH_PLAN_MISSION_CHARS,
)
from argus_skill.life.supervisor._mission_execution_runtime import (
    MissionExecutionRuntimeMixin,
)
from argus_skill.life.supervisor._planner_rendering import PlannerRenderingMixin


def _plan(objective: str = "test the mechanism") -> str:
    return f"""# Research plan
Objective: {objective}.

## Central hypotheses
1. [untested] The mechanism transfers — evidence: journal mission-a

## Experiment program
- Run the highest-information transfer test.
- **Bold bet:** invert the intervention.

## Established results
- Baseline is stable — evidence: results/baseline.json

## Dead ends
- Threshold tuning — abandoned because all seeds were flat.

## Next milestone
Independent replication strong enough to begin the paper.
"""


class _PlanHarness(PlannerRenderingMixin):
    def __init__(self, root) -> None:
        self.memory = SimpleNamespace(root=root)
        self.events: list[dict] = []

    def _emit(self, event) -> bool:
        self.events.append(event)
        return True


def test_plan_update_creates_file_atomically_and_journals_note(tmp_path) -> None:
    harness = _PlanHarness(tmp_path)

    assert harness._apply_research_plan_update("PLAN_UPDATE=" + _plan())
    assert (tmp_path / RESEARCH_PLAN_FILENAME).read_text(encoding="utf-8") == _plan()
    assert harness.events[-1]["text"] == "research plan updated"
    assert not list(tmp_path.glob(".RESEARCH_PLAN.md.*.tmp"))


def test_plan_update_replaces_existing_file(tmp_path) -> None:
    path = tmp_path / RESEARCH_PLAN_FILENAME
    path.write_text(_plan("old objective"), encoding="utf-8")
    harness = _PlanHarness(tmp_path)

    assert harness._apply_research_plan_update(
        "Decision:\nPROJECT_DONE=false\nPLAN_UPDATE: " + _plan("new objective")
    )
    assert "new objective" in path.read_text(encoding="utf-8")
    assert len(harness.events) == 1


def test_absent_plan_update_leaves_file_unchanged(tmp_path) -> None:
    path = tmp_path / RESEARCH_PLAN_FILENAME
    path.write_text(_plan(), encoding="utf-8")
    harness = _PlanHarness(tmp_path)

    assert not harness._apply_research_plan_update("PROJECT_DONE=false\nREASON=continue")
    assert path.read_text(encoding="utf-8") == _plan()
    assert harness.events == []


def test_malformed_plan_update_is_ignored_without_destroying_current_plan(tmp_path) -> None:
    path = tmp_path / RESEARCH_PLAN_FILENAME
    path.write_text(_plan(), encoding="utf-8")
    harness = _PlanHarness(tmp_path)

    assert not harness._apply_research_plan_update(
        "PLAN_UPDATE=# Research plan\nObjective: missing all required sections"
    )
    assert path.read_text(encoding="utf-8") == _plan()
    assert harness.events == []


def test_glued_plan_update_marker_is_tolerated(tmp_path) -> None:
    harness = _PlanHarness(tmp_path)

    assert harness._apply_research_plan_update(
        "The program changed direction.PLAN_UPDATE=" + _plan()
    )
    assert (tmp_path / RESEARCH_PLAN_FILENAME).is_file()


class _PreludeHarness(MissionExecutionRuntimeMixin):
    def __init__(self, root) -> None:
        self.memory = SimpleNamespace(
            root=root,
            render_prelude=lambda **_kwargs: "ordinary memory",
        )
        self.config = SimpleNamespace(runtime_context="")

    @staticmethod
    def _render_backlog_item_metadata(_item) -> str:
        return ""


def test_mission_prelude_contains_only_compact_plan_excerpt(tmp_path) -> None:
    plan = _plan()
    (tmp_path / RESEARCH_PLAN_FILENAME).write_text(plan, encoding="utf-8")
    item = BacklogItem.new(title="run transfer", objective="test transfer")

    prelude = _PreludeHarness(tmp_path)._build_mission_prelude(item)

    assert "## Research plan (mission excerpt)" in prelude
    assert "## Central hypotheses" in prelude
    assert "## Next milestone" in prelude
    assert "## Experiment program" not in prelude
    assert "## Established results" not in prelude
    assert "## Dead ends" not in prelude
    assert len(prelude.split("\n\n---\n\n", 1)[0]) <= RESEARCH_PLAN_MISSION_CHARS


def test_oversize_hypotheses_do_not_hide_mission_next_milestone(tmp_path) -> None:
    plan = _plan().replace(
        "The mechanism transfers",
        "The mechanism transfers " + "evidence " * 500,
    )
    (tmp_path / RESEARCH_PLAN_FILENAME).write_text(plan, encoding="utf-8")
    item = BacklogItem.new(title="run transfer", objective="test transfer")

    prelude = _PreludeHarness(tmp_path)._build_mission_prelude(item)

    excerpt = prelude.split("\n\n---\n\n", 1)[0]
    assert len(excerpt) <= RESEARCH_PLAN_MISSION_CHARS
    assert "## Central hypotheses" in excerpt
    assert "## Next milestone" in excerpt
