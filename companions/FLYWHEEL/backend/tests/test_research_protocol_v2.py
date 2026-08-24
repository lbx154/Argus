from __future__ import annotations

from copy import deepcopy

import pytest
from foundry.services.ideation import (
    RESEARCH_PROTOCOL_VERSION,
    compile_ideation_objective,
)
from foundry.services.prompt_compiler import PromptCompiler

TEAM = {
    "id": "team-protocol",
    "name": "Small causal systems team",
    "expertise": ["causal inference", "distributed systems"],
    "methods": ["controlled experiments", "systems measurement"],
    "data_access": ["public datasets and an authorized local testbed"],
    "constraints": {"people": 2, "elapsed_days": 60, "private_data": False},
    "goals": {"contribution": "mechanistic method", "aspiration": "Oral"},
    "policy": {"automatic_submission": False, "human_subjects": "not authorized"},
}
VENUE_SNAPSHOT = {
    "id": 1,
    "venue_key": "ICLR",
    "official_name": "International Conference on Learning Representations",
    "category_id": "ai",
    "metadata": {"track": "Main"},
}
DEADLINE_SNAPSHOT = {
    "id": 2,
    "conference_year": 2027,
    "deadline_date": "2026-09-25",
    "timezone": "AoE",
    "evidence_status": "forecast",
    "forecast_window_start": "2026-09-20",
    "forecast_window_end": "2026-09-30",
    "requires_confirmation": True,
    "source_url": "https://iclr.cc/",
}
RESOURCE_SNAPSHOT = {
    "id": "resource-1",
    "name": "One A6000",
    "resource_type": "gpu",
    "capacity": {"gpu_count": 1, "gpu_model": "RTX A6000", "gpu_hours": 80},
    "availability_state": "available",
    "enabled": True,
}


def _compile_ideation(**overrides: object):
    options = {
        "completion_target": (
            "Seek an oral-calibre mechanistic result, but stop with a documented "
            "negative result when the falsifier is met."
        ),
        **overrides,
    }
    return compile_ideation_objective(
        team_profile=TEAM,
        venue=VENUE_SNAPSHOT,
        deadline=DEADLINE_SNAPSHOT,
        resource=RESOURCE_SNAPSHOT,
        run_options=options,
    )


def test_conditioned_protocol_defaults_to_ten_without_making_it_a_quota() -> None:
    compiled = _compile_ideation()
    run = compiled.condition_snapshot["run"]

    assert compiled.condition_snapshot["research_protocol_version"] == RESEARCH_PROTOCOL_VERSION
    assert run["candidate_target"] == 10
    assert run["candidate_count"] == 10  # compatibility field
    assert run["candidate_padding_forbidden"] is True
    assert "target of 10 is a discovery target, not a quota" in compiled.objective
    assert "Fewer candidates, zero finalists" in compiled.objective
    assert "NO_WINNER" in compiled.objective


def test_free_form_goal_is_compiled_into_gates_budget_and_stop_criteria() -> None:
    compiled = _compile_ideation(candidate_count=7, finalist_count=3)
    contract = compiled.condition_snapshot["run"]["goal_contract"]

    assert contract["operator_aspiration"].startswith("Seek an oral-calibre")
    assert len(contract["measurable_gates"]) == 6
    assert contract["budget"]["resource_snapshot_bound"] is True
    assert contract["budget"]["resource_ceiling"]["capacity"]["gpu_hours"] == 80
    assert contract["stop_criteria"]
    assert "NEGATIVE_RESULT_RECORDED" in contract["valid_non_positive_outcomes"]
    assert "machine-checkable contract" in compiled.objective
    assert "The operator target cannot override" in compiled.objective


def test_project_protocol_contains_independent_roles_two_reviews_and_human_gates() -> None:
    objective = _compile_ideation().objective

    for marker in (
        "DEBATER_A_BUILDER",
        "DEBATER_B_BREAKER",
        "ARBITER",
        "HUMAN_GATE_0_CONDITIONS",
        "HUMAN_GATE_1_SELECTION",
        "INDEPENDENT_REVIEW_1",
        "INDEPENDENT_REVIEW_2",
        "five new fresh-context reviewers",
        "FINAL_INTEGRITY_CHECK",
        "HUMAN_GATE_2_FINAL",
        "REPRODUCIBILITY_MANIFEST",
        "Argus code SHA",
        '"search_cutoff"',
        '"closest_source_ids"',
        '"falsifier"',
    ):
        assert marker in objective


def test_condition_hash_binds_goal_and_is_deterministic() -> None:
    first = _compile_ideation()
    repeated = _compile_ideation()
    changed = _compile_ideation(completion_target="A different bounded objective.")

    assert first.condition_sha256 == repeated.condition_sha256
    assert first.objective_sha256 == repeated.objective_sha256
    assert changed.condition_sha256 != first.condition_sha256
    assert changed.objective_sha256 != first.objective_sha256


VENUE = {
    "name": "ICLR",
    "edition": 2027,
    "track": "Main",
    "deadline": "2026-09-25 AoE (forecast; confirm before launch)",
    "scope": "representation learning",
}
DOMAIN = {
    "name": "AI",
    "evidence_requirements": ["fixed split", "compute-matched baselines"],
}
IDEA = {
    "title": "Interventional task grammar",
    "problem_gap": "correlations do not establish a causal task representation",
    "mechanism_hypothesis": "selective interventions alter only the target rule",
    "public_data_or_tasks": "public benchmark@frozen-version",
    "kill_criterion": "random subspaces are equally effective",
    "completion_target": "Aim for an original oral-quality result with honest negative outcomes.",
    "candidate_target": 10,
    "finalist_limit": 4,
}
RESOURCES = {
    "gpu_count": 1,
    "gpu_model": "RTX A6000",
    "gpu_hours": 80,
    "api_budget": "2M tokens",
    "max_parallel_jobs": 1,
    "wall_clock_deadline": "2026-09-01T00:00:00Z",
}


def test_idea_prompt_compiler_binds_team_goal_and_protocol_provenance() -> None:
    compiled = PromptCompiler().compile(
        venue=VENUE,
        domain=DOMAIN,
        idea=IDEA,
        resources=RESOURCES,
        team=TEAM,
    )

    assert compiled.manifest["research_protocol_version"] == RESEARCH_PROTOCOL_VERSION
    assert compiled.manifest["condition_snapshot_bound"] is True
    assert compiled.manifest["execution_eligible"] is False
    assert compiled.manifest["personalization_state"] == "seed_or_unbound_preview"
    assert compiled.manifest["positive_result_required"] is False
    assert compiled.manifest["automatic_submission_allowed"] is False
    assert compiled.manifest["goal_contract"]["hard_budget"]["gpu_hours"] == 80
    assert len(compiled.manifest["input_sha256"]) == 64
    assert len(compiled.manifest["prompt_sha256"]) == 64
    for marker in (
        "ARGUS / FLYWHEEL",
        "Builder / Breaker / Arbiter",
        "五位 fresh-context",
        "五位新的 fresh-context",
        "FINAL INTEGRITY CHECK",
        "REPRODUCIBILITY_MANIFEST",
        "NO_WINNER",
        "NEGATIVE_RESULT_RECORDED",
        "不得为达到 10 条而填充弱 idea",
    ):
        assert marker in compiled.prompt


def test_prompt_hash_changes_with_team_conditions() -> None:
    compiler = PromptCompiler()
    first = compiler.compile(
        venue=VENUE, domain=DOMAIN, idea=IDEA, resources=RESOURCES, team=TEAM
    )
    other_team = deepcopy(TEAM)
    other_team["expertise"] = ["formal methods"]
    second = compiler.compile(
        venue=VENUE, domain=DOMAIN, idea=IDEA, resources=RESOURCES, team=other_team
    )

    assert first.manifest["input_sha256"] != second.manifest["input_sha256"]
    assert first.prompt_sha256 != second.prompt_sha256


def test_prompt_is_execution_eligible_only_with_conditioned_candidate_binding() -> None:
    bound_idea = {
        **IDEA,
        "condition_binding": {
            "ideation_run_id": "run-1",
            "candidate_id": "candidate-1",
            "condition_sha256": "a" * 64,
            "parent_objective_sha256": "b" * 64,
            "candidate_artifact_sha256": "c" * 64,
        },
    }
    compiled = PromptCompiler().compile(
        venue=VENUE,
        domain=DOMAIN,
        idea=bound_idea,
        resources=RESOURCES,
        team=TEAM,
    )

    assert compiled.manifest["execution_eligible"] is True
    assert compiled.manifest["personalization_state"] == "conditioned_ideation_candidate"
    assert compiled.manifest["execution_blockers"] == []
    assert "候选已绑定到条件化 ideation run" in compiled.prompt


@pytest.mark.parametrize("candidate_target", [2, 21, True, "10"])
def test_prompt_compiler_rejects_invalid_candidate_target(candidate_target: object) -> None:
    idea = {**IDEA, "candidate_target": candidate_target}

    with pytest.raises(ValueError, match="candidate_target"):
        PromptCompiler().compile(
            venue=VENUE, domain=DOMAIN, idea=idea, resources=RESOURCES, team=TEAM
        )
