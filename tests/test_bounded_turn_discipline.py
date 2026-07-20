"""Regression: the engineer mission prompt must instruct bounded-progress turns.

Each fresh Engineer turn should land a coherent increment, update the shared
checkpoint, and yield. This file also guards the fixed prompt against token
re-bloat.

These tests lock in that the turn-discipline / bounded-progress contract is
present in the engineer prompt, for both paper and non-paper missions.
"""
import pytest

from argus_skill.loop import SkillLoop


@pytest.fixture(autouse=True)
def _isolate_project_vertical_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ARGUS_SKILL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.chdir(tmp_path)


def _prompt(task: str, *, paper_mission: bool = False) -> str:
    # paper_mission kept as a test label only — the prompt no longer branches on
    # it (turn discipline is unconditional), so both call styles assert the same
    # contract holds.
    return SkillLoop._build_engineer_prompt(
        task=task,
        skill_text="",
        next_action=None,
    )


def test_bounded_turn_discipline_present_for_paper_mission():
    out = _prompt(
        "Work the benchmark stage of the EMNLP paper: build the dataset "
        "evidence package and resolve all readiness blockers.",
        paper_mission=True,
    )
    assert "## This turn" in out
    # Must tell the engineer to stop after a bounded increment and yield.
    assert "yield" in out.lower()
    assert "one coherent, verifiable increment" in out.lower()
    assert "pure reading" in out.lower()


def test_turn_discipline_present_even_for_nonpaper_task():
    # The bounded-progress contract is universal (it guards context growth for
    # any mission), not gated on the paper-objective heuristic.
    out = _prompt("Refactor the data loader and add unit tests.")
    assert "## This turn" in out
    assert "do not write planning/spec/brief" in out.lower()
    assert "initialize git" in out.lower()
    assert "commit" in out.lower()
    assert "spawn subagents" in out.lower()


@pytest.mark.parametrize("allow_self_review", [False, True])
def test_engineer_must_not_spawn_a_subagent_to_impersonate_reviewer(
    allow_self_review: bool,
):
    out = SkillLoop._build_engineer_prompt(
        task="Repair the run contract and request independent review.",
        skill_text="",
        next_action=None,
        allow_self_review=allow_self_review,
    )

    assert "reviewer subagent" in out.lower()
    assert "fresh reviewer session" in out.lower()
    assert "yield" in out.lower()


def test_engineer_fixed_prompt_stays_token_efficient():
    assert len(_prompt("Refactor the data loader and add unit tests.")) < 1_600
