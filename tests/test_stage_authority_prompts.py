"""Guard tests: pipeline stage-transition authority belongs to the Manager only.

These lock the prompt surgery (Step 4 of the stage-authority change) that removed
every instruction telling the engineer / reviewer / planner to write pipeline
stage state or call ``rollback_stage`` directly. The Manager is the sole
writer of ``current_stage`` after initial state creation; the others only advise.
"""

from __future__ import annotations

from pathlib import Path

import argus

ROOT = Path(argus.__file__).resolve().parent

# The specific agent-facing shell recipe the prompts used to emit. Its absence is
# the regression guard (a passing comment mentioning rollback_stage won't match
# this full call shape).
_ROLLBACK_RECIPE = "rollback_stage('.', target_stage="


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")




def test_planner_source_has_no_rollback_recipe() -> None:
    assert _ROLLBACK_RECIPE not in _src("planner/planner.py")


def test_reviewer_reports_upstream_defects_instead_of_rolling_back() -> None:
    src = _src("roles/prompts/reviewer.py")
    assert _ROLLBACK_RECIPE not in src
    assert "the Planner, who proposes reopening" in src


def test_experiment_playbook_does_not_tell_engineer_to_advance_stage() -> None:
    md = _src("verticals/research/skills/research-experiment-playbook.md")
    assert "advance to the next stage and update" not in md
    # One owner per decision: the completion gate moves the stage.
    assert "The completion gate alone advances the stage" in md


def test_experiment_measures_the_unmodified_model_before_building_the_method() -> None:
    """s-efe9deac measured its step-0 policy 34 h in, on the operator's order,
    and a trained arm then scored below it; a framework run found its base model
    at 180/180 only in its last task; an RSI run chose an environment where the
    base model scored 0. The starting point comes first, from the real evaluator."""
    from argus.verticals.research.prompt_policy import _method_card_planner_block

    md = _src("verticals/research/skills/research-experiment-playbook.md")
    assert "Measure the starting point first" in md
    assert "unmodified model or system through the real evaluator" in md
    assert "first number is the unmodified model" in _method_card_planner_block("experiment")


def test_the_shared_runner_runs_every_arm_on_real_data_before_the_arms_start() -> None:
    """s-5dc7ab4a (10-27 23:18-00:12): the runner passed its engineering config,
    then the parallel arms, which may not edit it, met two runner defects on the
    real development panel (official metadata the records could not serialise,
    an analysis entry refusing one arm); each cost a hand-back, a replan, a
    repair task and a rerun."""
    from argus.verticals.research.prompt_policy import _method_card_planner_block

    block = _method_card_planner_block("experiment")
    assert "runs each arm's own config one short step on the real development data" in block
    assert "through the analysis the comparison uses" in block
