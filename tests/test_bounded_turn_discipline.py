"""Regression: the engineer mission prompt must instruct bounded-progress turns.

Each fresh Engineer turn should land a coherent increment, update the shared
checkpoint, and yield. This file also guards the fixed prompt against token
re-bloat.

These tests lock in that the turn-discipline / bounded-progress contract is
present in the engineer prompt, for both paper and non-paper missions.
"""

import pytest

from argus.core.handoff import form_tool
from argus.loop import SkillLoop
from tests.role_rules import lists, rule


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
    assert "CHECKPOINT.md there: your state from one round to the next" in out
    assert "one coherent, verifiable increment" not in out


def test_long_experiment_protocol_is_in_every_engineer_turn():
    full = _prompt("Run one bounded GPU experiment.")
    compact = SkillLoop._build_engineer_prompt(
        task="Run one bounded GPU experiment.",
        skill_text="",
        next_action="Collect the pending run.",
        include_static=False,
    )

    assert lists(full, "long-runs-and-evidence/SKILL.md")
    assert "## Continuation turn" in compact  # the session already holds the page
    text = rule("engineer", "long-runs-and-evidence/SKILL.md")
    assert "setsid nohup" in text
    assert "blocking call" in text
    assert "Never poll in short loops" in text


def test_engineer_must_not_spawn_a_subagent_to_impersonate_reviewer():
    out = SkillLoop._build_engineer_prompt(
        task="Repair the run contract and request independent review.",
        skill_text="",
        next_action=None,
    )

    assert "End your turn with hand_off" in out
    assert "do not start one yourself or through a subagent" in form_tool("engineer")["description"]
    assert "yield_time_ms" in rule("engineer", "long-runs-and-evidence/SKILL.md")


def test_performance_claims_require_causal_attribution() -> None:
    full = _prompt("Diagnose the data throughput bottleneck.")
    compact = SkillLoop._build_engineer_prompt(
        task="Diagnose the data throughput bottleneck.",
        skill_text="",
        next_action="Continue the causal diagnosis.",
        include_static=False,
    )

    assert lists(full, "long-runs-and-evidence/SKILL.md")
    assert "## Continuation turn" in compact
    assert "hot-path or live-resource evidence plus timing, profiling or a controlled A/B" in rule(
        "engineer", "long-runs-and-evidence/SKILL.md")


def test_engineer_checkpoint_states_its_purpose_without_a_ban():
    out = _prompt("Continue the implementation across rounds.")

    assert "CHECKPOINT.md there: your state from one round to the next" in out
    assert "do not create separate summaries" not in out
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
    team = SkillLoop._build_engineer_prompt(
        task="Continue until an operator-owned choice is required.",
        skill_text="",
        next_action=None,
        compact_team=True,
    )

    for out in (full, compact, team):
        assert "End your turn with hand_off" in out
        assert "OPERATOR_OPTIONS" not in out
    assert {"operator", "done"} <= set(form_tool("engineer")["inputSchema"]["properties"]["to"]["enum"])


def test_engineer_fixed_prompt_stays_token_efficient():
    # The shared writing voice, durable accelerator admission, and recoverable
    # background-result contract are intentional fixed costs. Keep only modest
    # headroom for another static section.
    assert len(_prompt("Refactor the data loader and add unit tests.")) < 5_400
