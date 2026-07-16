"""Tests for Manager domain authoring parser (``manager/domain_author``)."""
from __future__ import annotations

import json

from argus_skill.manager.domain_author import (
    DomainProposal,
    build_domain_author_prompt,
    build_fast_vertical_decision_prompt,
    build_vertical_decision_prompt,
    parse_domain_proposal,
    parse_fast_vertical_decision,
)
from argus_skill.skills.vertical_select import VERTICAL_PURPOSES, VERTICALS


def test_parse_happy_path():
    raw = json.dumps({
        "name": "robotics_sim",
        "stages": ["scope", "simulate", "measure", "report"],
        "rationale": "novel control domain",
        "confidence": 0.8,
    })
    p = parse_domain_proposal(raw, known_verticals=VERTICALS)
    assert isinstance(p, DomainProposal)
    assert p.name == "robotics_sim"
    assert p.stages == ["scope", "simulate", "measure", "report"]


def test_parse_sluggifies_name_and_stages():
    raw = json.dumps({"name": "Robotics Sim!", "stages": ["Scope Phase", "Sim-Run"]})
    p = parse_domain_proposal(raw, known_verticals=VERTICALS)
    assert p.name == "robotics_sim"
    assert p.stages == ["scope_phase", "sim_run"]


def test_parse_fail_closed_on_bad_json():
    assert parse_domain_proposal("not json", known_verticals=VERTICALS) is None
    assert parse_domain_proposal("{}", known_verticals=VERTICALS) is None


def test_parse_rejects_too_few_or_too_many_stages():
    assert parse_domain_proposal(json.dumps({"name": "x", "stages": ["only_one"]}),
                                 known_verticals=VERTICALS) is None
    big = {"name": "x", "stages": [f"s{i}" for i in range(20)]}
    assert parse_domain_proposal(json.dumps(big), known_verticals=VERTICALS) is None


def test_parse_dedupes_name_against_known_and_existing():
    # Collision with a preset vertical → suffixed, not rejected.
    raw = json.dumps({"name": "research", "stages": ["a", "b"]})
    p = parse_domain_proposal(raw, known_verticals=VERTICALS, existing_data_domains=["research_2"])
    assert p is not None and p.name not in ("research", "research_2")


def test_prompt_mentions_known_and_existing():
    prompt = build_domain_author_prompt(
        "build a control loop", known_verticals=["research", "quant"],
        existing_data_domains=["robotics_sim"],
    )
    assert "research" in prompt and "quant" in prompt and "robotics_sim" in prompt
    assert "JSON" in prompt


def test_prompt_instructs_grounded_investigation_not_blind_guess():
    """Regression: the Manager must be told to actually inspect the repo
    (shell access, read-only) before proposing a stage skeleton, instead of
    guessing a generic template from the task sentence alone."""
    prompt = build_domain_author_prompt(
        "optimize the slowest function", known_verticals=["research"],
    )
    assert "shell access" in prompt.lower()
    assert "investigate" in prompt.lower()
    assert "READ-ONLY" in prompt
    assert "do NOT edit" in prompt


def test_vertical_prompt_keeps_math_routes_inside_builtin_math():
    prompt = build_vertical_decision_prompt(
        "Investigate an open conjecture with literature, computation, proof, and review",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "stable, reusable capability contract" in prompt
    assert "`math_conjecture`" in prompt
    assert "dynamic Planner backlog/DAG tasks" in prompt
    assert "they are not competing verticals" in prompt


def test_vertical_prompt_does_not_escalate_bounded_repo_fix_to_new_domain() -> None:
    prompt = build_vertical_decision_prompt(
        "Repair one failing test in the current repository and return the patch.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "short-cycle software repairs" in prompt
    assert "Do not choose a staged lifecycle or author a new domain" in prompt
    assert "repo investigation alone does not make the task long-horizon" in prompt.lower()


def test_fast_vertical_prompt_is_tool_free_and_route_only() -> None:
    prompt = build_fast_vertical_decision_prompt(
        "Repair one failing test in the current repository.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "NO tools" in prompt
    assert "choose Live View" in prompt
    assert "expand the task" in prompt
    assert "execution_task" not in prompt
    assert "shell access" not in prompt


def test_fast_vertical_parser_accepts_confident_existing_route() -> None:
    route = parse_fast_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "direct",
            "confidence": 0.94,
            "research_target_level": None,
            "rationale": "bounded repair",
        }),
        known_verticals=VERTICALS,
    )

    assert route is not None
    assert route.needs_grounding is False
    assert route.vertical == "direct"
    assert route.confidence == 0.94


def test_fast_vertical_parser_sends_new_or_uncertain_work_to_grounding() -> None:
    route = parse_fast_vertical_decision(
        json.dumps({
            "choice": "grounded",
            "confidence": 0.4,
            "rationale": "repository structure matters",
        }),
        known_verticals=VERTICALS,
    )

    assert route is not None
    assert route.needs_grounding is True


def test_grounded_vertical_prompt_has_bounded_inspection_and_no_rendering_work() -> None:
    prompt = build_vertical_decision_prompt(
        "Build a novel controller whose repository structure is unknown.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "ONE focused inspection batch" in prompt
    assert "at most four file/search operations" in prompt
    assert "choose Live View artifacts" in prompt
    assert "expand the Engineer task" in prompt
    assert "presentations" not in prompt
    assert "execution_task" not in prompt
