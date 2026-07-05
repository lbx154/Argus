from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from argus_skill.planner.planner import Planner
from argus_skill.wiki.bot_state import BotState, save_bot_state


@pytest.fixture(autouse=True)
def _persist_research_vertical(tmp_path: Path) -> None:
    # Real missions persist the Manager-decided vertical before the planner runs;
    # resolve_vertical is fail-hard, so seed research for these synthetic projects.
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")


def _make_wiki(tmp_path: Path, project: str = "demo") -> Path:
    wiki = tmp_path / ".autors" / project / "wiki"
    (wiki / "queries").mkdir(parents=True)
    (wiki / "data").mkdir(parents=True)
    (wiki / "query_pack.md").write_text("# pack")
    return wiki


def test_planner_suggests_wiki_collect_when_cooldown_elapsed_and_backlog_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = _make_wiki(tmp_path)
    save_bot_state(
        wiki / "data" / "bot_state.json",
        BotState(
            last_collected_at=datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        ),
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "wiki_collect" in prompt
    assert "cooldown" in prompt.lower()


def test_planner_does_not_suggest_wiki_collect_when_cooldown_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    wiki = _make_wiki(tmp_path)
    # Just collected 1 hour ago -- cooldown still active.
    save_bot_state(
        wiki / "data" / "bot_state.json",
        BotState(last_collected_at=datetime.now(timezone.utc) - timedelta(hours=1)),
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective="research X",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )
    assert "wiki_collect" not in prompt
