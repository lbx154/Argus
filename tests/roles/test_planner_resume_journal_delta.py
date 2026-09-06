"""The resume prompt can carry only what the session has not read yet.

A resumed Planner session already holds the journal window and the research
plan from its earlier turns. ``build_continuous_resume_prompt`` therefore
accepts the journal as a delta (only newly settled entries, or an explicit
"nothing new" line) and a marker that the research plan is unchanged, instead
of repeating both documents on every cycle.
"""

from __future__ import annotations

from pathlib import Path

from argus_skill.roles.prompts.planner import build_continuous_resume_prompt
from argus_skill.skills.vertical_select import persist_vertical


def _resume(tmp_path: Path, **kwargs) -> str:
    return build_continuous_resume_prompt(
        continuous_objective="improve the solver",
        planning_cycle=3,
        project_root=tmp_path,
        state_root=tmp_path,
        **kwargs,
    )


def test_full_journal_keeps_the_original_heading(tmp_path: Path) -> None:
    prompt = _resume(tmp_path, journal_tail="- [09-06 10:00] mission_complete: run A")
    assert "## Journal of completed work (most recent last)" in prompt
    assert "mission_complete: run A" in prompt
    assert "Newly settled work" not in prompt


def test_journal_delta_renders_only_the_new_entries(tmp_path: Path) -> None:
    prompt = _resume(
        tmp_path,
        journal_tail="- [09-06 10:00] mission_complete: run B",
        journal_is_delta=True,
    )
    assert (
        "## Newly settled work since your previous planning turn" in prompt
    )
    assert "mission_complete: run B" in prompt
    assert "## Journal of completed work" not in prompt


def test_empty_journal_delta_says_nothing_new_settled(tmp_path: Path) -> None:
    prompt = _resume(tmp_path, journal_tail="", journal_is_delta=True)
    assert "nothing new has settled since your previous planning turn" in prompt
    assert "no completed work yet" not in prompt


def test_empty_full_journal_still_reads_as_the_first_cycle(tmp_path: Path) -> None:
    prompt = _resume(tmp_path, journal_tail="")
    assert "no completed work yet — this is the first cycle" in prompt


def test_unchanged_research_plan_becomes_a_marker(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software")
    plan = "# Research plan\nEverything hinges on the solver rewrite."
    unchanged = _resume(
        tmp_path,
        journal_tail="",
        research_plan=plan,
        research_plan_unchanged=True,
    )
    assert "unchanged since your previous planning turn" in unchanged
    assert "Everything hinges on the solver rewrite" not in unchanged

    changed = _resume(tmp_path, journal_tail="", research_plan=plan)
    assert "Everything hinges on the solver rewrite" in changed
