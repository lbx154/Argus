from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.planner.planner import Planner


def test_planner_prompt_includes_wiki_block_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "query_pack.md").write_text("# pack\nhello\n")
    (wiki / "queries" / "stale-watchlist.md").write_text("# stale\noverdue\n")
    (wiki / "queries" / "open-contradictions.md").write_text("# open\nc1\n")

    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "Idea wiki" in prompt
    assert "stale-watchlist" in prompt or "overdue" in prompt
    assert "open-contradictions" in prompt or "c1" in prompt


def test_planner_prompt_surfaces_by_status_so_learned_pages_reach_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A freshly learned technique page shows up only in queries/by-status.md
    (never in the static query_pack.md), so the planner must inject by-status."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "query_pack.md").write_text("# pack\nhow-to-use protocol only\n")
    (wiki / "queries" / "by-status.md").write_text(
        "# Cards by status\n\n## candidate\n- `technique/grpo-async-clip` -- Async clip\n"
    )

    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "by-status.md" in prompt
    assert "grpo-async-clip" in prompt


def test_planner_prompt_omits_wiki_block_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "Idea wiki" not in prompt


def test_planner_prompt_does_not_warn_when_query_pack_diagnosis_refs_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (tmp_path / "diagnosis").mkdir()
    (tmp_path / "diagnosis" / "operator_only_external_blocker_lock_20260605.json").write_text("{}\n")
    (tmp_path / "diagnosis" / "stage_check_terminal_manifest_20260605.json").write_text("{}\n")
    (wiki / "query_pack.md").write_text(
        "Read diagnosis/operator_only_external_blocker_lock_20260605.json and "
        "diagnosis/stage_check_terminal_manifest_20260605.json before reentry.\n"
    )

    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )

    assert "missing diagnosis refs from query_pack.md" not in prompt


def test_planner_prompt_warns_when_query_pack_diagnosis_refs_are_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "query_pack.md").write_text(
        "Read diagnosis/operator_only_external_blocker_lock_20260605.json before reentry.\n"
    )

    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )

    assert "missing diagnosis refs from query_pack.md" in prompt
    assert "diagnosis/operator_only_external_blocker_lock_20260605.json" in prompt


def test_planner_prompt_build_survives_corrupt_bot_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "data").mkdir()
    (wiki / "query_pack.md").write_text("# pack\n")
    (wiki / "data" / "bot_state.json").write_text("{", encoding="utf-8")

    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )

    assert "Idea wiki" in prompt
    assert list((wiki / "data").glob("bot_state.json.corrupt-*"))
