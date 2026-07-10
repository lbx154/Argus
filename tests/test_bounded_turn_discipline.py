"""Regression: the engineer mission prompt must instruct bounded-progress turns.

Root cause of the cost/latency blowup: a single mission ran one codex `exec`
for ~3400 internal turns, growing context until codex auto-compacted ~1094
times (the rollout was 1.16 GB, 95% compaction snapshots). The existing
session-roll (shift_round_limit) never engaged because the whole mission was a
single round. The fix makes each turn land one bounded increment and yield, so
the round ends, the reviewer updates the checkpoint, and the session rolls with
a small context instead of bloating.

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
    assert "## Turn discipline" in out
    # Must tell the engineer to stop after a bounded increment and yield.
    assert "yield" in out.lower()
    assert "one concrete increment" in out.lower()
    # Must warn that pure exploration risks the no-progress abort.
    assert "no forward progress" in out.lower()


def test_turn_discipline_present_even_for_nonpaper_task():
    # The bounded-progress contract is universal (it guards context growth for
    # any mission), not gated on the paper-objective heuristic.
    out = _prompt("Refactor the data loader and add unit tests.")
    assert "## Turn discipline" in out
