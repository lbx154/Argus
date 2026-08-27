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


def test_checkpoint_handoff_discipline_present_for_paper_mission():
    out = _prompt(
        "Work the benchmark stage of the EMNLP paper: build the dataset "
        "evidence package and resolve all readiness blockers.",
        paper_mission=True,
    )
    assert "## This turn" in out
    assert "pure reading" in out.lower()
    assert "CHECKPOINT.md is the only role-maintained cross-round handoff file" in out
    assert "one coherent, verifiable increment" not in out


def test_turn_discipline_present_even_for_nonpaper_task():
    # The bounded-progress contract is universal (it guards context growth for
    # any mission), not gated on the paper-objective heuristic. Engineer used to
    # be told not to spawn subagents, which -- next to a 24-call ceiling and no
    # authority to re-plan -- left wide work with nowhere to go but its own
    # context. Delegation is now the way that ceiling is respected.
    out = _prompt("Refactor the data loader and add unit tests.")
    assert "## This turn" in out
    assert "do not write planning/spec/brief" in out.lower()
    assert "initialize git" in out.lower()
    assert "commit" in out.lower()
    assert "planner owns the campaign plan" in out.lower()
    assert "delegate wide" in out.lower()
    assert "the answer, not the transcript" in out.lower()


def test_long_experiment_protocol_is_in_every_engineer_turn():
    full = _prompt("Run one bounded GPU experiment.")
    compact = SkillLoop._build_engineer_prompt(
        task="Run one bounded GPU experiment.",
        skill_text="",
        next_action="Collect the pending run.",
        include_static=False,
    )

    for out in (full, compact):
        assert "argus_skill.tools.subagent submit" in out
        assert "--mode direct" in out
        assert "--mode supervised" in out
        assert "launch a supervised subagent" not in out
        assert "session-owned background shell" in out.lower()
        assert "do not poll in the foreground" in out.lower()


def test_engineer_must_not_spawn_a_subagent_to_impersonate_reviewer():
    out = SkillLoop._build_engineer_prompt(
        task="Repair the run contract and request independent review.",
        skill_text="",
        next_action=None,
    )

    assert "reviewer subagent" in out.lower()
    assert "host invokes reviewer only when required" in out.lower()
    assert "yield" in out.lower()


def test_performance_claims_require_causal_attribution() -> None:
    full = _prompt("Diagnose the data throughput bottleneck.")
    compact = SkillLoop._build_engineer_prompt(
        task="Diagnose the data throughput bottleneck.",
        skill_text="",
        next_action="Continue the causal diagnosis.",
        include_static=False,
    )

    for out in (full, compact):
        assert "Performance root-cause/bottleneck/replacement claims need" in out
        assert "hot-path/live-resource evidence plus timing/profiling or controlled A/B" in out


def test_engineer_does_not_create_extra_handoff_packets():
    out = _prompt("Continue the implementation across rounds.")

    assert "only role-maintained cross-round handoff file" in out
    assert "do not create handoff or evidence packets" in out
    assert "compile/type-check" not in out
    assert "git ls-files --error-unmatch" not in out


def test_engineer_surfaces_operator_only_blockers_to_host():
    full = _prompt("Continue until an operator-owned choice is required.")
    compact = SkillLoop._build_engineer_prompt(
        task="Continue until an operator-owned choice is required.",
        skill_text="",
        next_action="Ask only if the operator owns the decision.",
        include_static=False,
    )

    for out in (full, compact):
        assert "operator_question" in out
        assert "operator_options" in out
        assert "MILESTONE_STATUS=done" in out
        assert "parks the task" in out or "Never keep opening fresh rounds" in out


def test_engineer_fixed_prompt_stays_token_efficient():
    assert len(_prompt("Refactor the data loader and add unit tests.")) < 2_500
