"""The research notes replace the older HANDOFF.md without disturbing a live project."""
from __future__ import annotations

from pathlib import Path

from argus.verticals.research.notes import (
    LEGACY_NOTES_FILENAME,
    RESEARCH_NOTES_FILENAME,
    clear_research_notes,
    notes_heading,
    read_research_notes,
    research_notes_path,
)
from argus.verticals.research.prompt_policy import (
    active_context_paths,
    active_research_context,
)


def test_headings_name_the_stage_in_plain_words() -> None:
    assert notes_heading("idea") == "# Research notes — Idea stage"
    assert notes_heading("experiment") == "# Research notes — Experiment stage"
    assert notes_heading("paper") == "# Research notes — Paper stage"
    assert notes_heading("review") == "# Research notes"


def test_legacy_file_is_moved_to_the_new_name_on_first_read(tmp_path: Path) -> None:
    legacy = tmp_path / LEGACY_NOTES_FILENAME
    legacy.write_text("# HANDOFF — EXPERIMENT\n\nThe thesis.", encoding="utf-8")

    assert read_research_notes(tmp_path).endswith("The thesis.")
    assert not legacy.exists()
    assert (tmp_path / RESEARCH_NOTES_FILENAME).read_text(encoding="utf-8").endswith(
        "The thesis."
    )
    assert research_notes_path(tmp_path) == tmp_path / RESEARCH_NOTES_FILENAME


def test_current_notes_win_when_both_names_exist(tmp_path: Path) -> None:
    (tmp_path / LEGACY_NOTES_FILENAME).write_text("old", encoding="utf-8")
    (tmp_path / RESEARCH_NOTES_FILENAME).write_text("current", encoding="utf-8")
    assert read_research_notes(tmp_path) == "current"
    assert (tmp_path / LEGACY_NOTES_FILENAME).exists()


def test_clearing_removes_both_names(tmp_path: Path) -> None:
    (tmp_path / LEGACY_NOTES_FILENAME).write_text("old", encoding="utf-8")
    (tmp_path / RESEARCH_NOTES_FILENAME).write_text("current", encoding="utf-8")
    clear_research_notes(tmp_path)
    assert not (tmp_path / LEGACY_NOTES_FILENAME).exists()
    assert not (tmp_path / RESEARCH_NOTES_FILENAME).exists()


def test_stage_context_loads_the_notes_under_the_new_name(tmp_path: Path) -> None:
    assert active_context_paths("experiment") == (RESEARCH_NOTES_FILENAME,)
    (tmp_path / LEGACY_NOTES_FILENAME).write_text(
        "# HANDOFF — EXPERIMENT\n\nUNIQUE_THESIS_LINE", encoding="utf-8"
    )
    block = active_research_context("experiment", tmp_path)
    assert "UNIQUE_THESIS_LINE" in block
    assert f"`{RESEARCH_NOTES_FILENAME}`" in block
    assert (tmp_path / RESEARCH_NOTES_FILENAME).exists()


def test_team_workers_read_the_notes_and_leave_them_to_the_dispatching_mission(
    monkeypatch,
) -> None:
    from argus.verticals.research import stages

    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)
    solo = stages.role_banner("engineer")
    assert "rewrite the research notes, RESEARCH_NOTES.md" in solo

    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "research-idea-pipeline-v8-g1-route-03")
    worker = stages.role_banner("engineer")
    assert "rewrite the research notes" not in worker
    assert "leave them unchanged" in worker
    assert worker.startswith(solo.split(" and rewrite the research notes")[0])
    assert stages.role_banner("reviewer") == stages._REVIEWER_RESEARCH_JUDGEMENT
