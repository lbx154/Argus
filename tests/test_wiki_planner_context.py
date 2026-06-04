from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.planner.planner import Planner


def test_planner_prompt_includes_wiki_block_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
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


def test_planner_prompt_omits_wiki_block_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        budget_remaining_usd=10.0,
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "Idea wiki" not in prompt
