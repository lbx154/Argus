"""Tests for Manager domain authoring parser (``manager/domain_author``)."""
from __future__ import annotations

import json

from argus_skill.domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
from argus_skill.manager.domain_author import (
    DomainProposal,
    build_domain_author_prompt,
    build_fast_vertical_decision_prompt,
    build_vertical_decision_prompt,
    parse_domain_proposal,
    parse_fast_vertical_decision,
    parse_vertical_decision,
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
    # The prompt still has to state its output shape; since 2026-07-26 that
    # shape is named lines rather than a JSON schema.
    assert "NAME=<slug>" in prompt
    assert "STAGES=" in prompt
    assert "JSON" not in prompt


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


def test_vertical_prompt_composes_chemistry_with_research() -> None:
    prompt = build_vertical_decision_prompt(
        "Run autonomous chemistry research and produce a paper",
        verticals_with_purpose=VERTICAL_PURPOSES,
        domains_with_purpose=DOMAIN_PURPOSES,
    )

    assert "`domain=chemistry`" in prompt
    # The reply convention is named lines, not a JSON schema: no role is forced
    # to serialise its answer (operator directive 2026-07-26).
    assert "DOMAIN=<built-in research domain, or none>" in prompt
    assert "JSON" not in prompt


def test_vertical_prompt_does_not_escalate_bounded_repo_fix_to_new_domain() -> None:
    prompt = build_vertical_decision_prompt(
        "Repair one failing test in the current repository and return the patch.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "capability VERTICAL" in prompt
    assert "workflow_mode" in prompt
    assert "software" in prompt


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
            "vertical": "software",
            "workflow_mode": "direct",
            "confidence": 0.94,
            "research_target_level": None,
            "rationale": "bounded repair",
        }),
        known_verticals=VERTICALS,
    )

    assert route is not None
    assert route.needs_grounding is False
    assert route.vertical == "software"
    assert route.workflow_mode == "direct"
    assert route.confidence == 0.94


def test_fast_vertical_parser_accepts_research_with_chemistry_domain() -> None:
    route = parse_fast_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "research",
            "domain": "chemistry",
            "workflow_mode": "staged",
            "confidence": 0.97,
            "research_target_level": "publishable",
            "rationale": "chemistry paper",
        }),
        known_verticals=VERTICALS,
        known_domains=BUILTIN_DOMAINS,
        research_target_verticals=("research",),
    )

    assert route is not None
    assert route.vertical == "research"
    assert route.domain == "chemistry"


def test_vertical_parser_rejects_domain_on_non_research_workflow() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "software",
            "domain": "chemistry",
            "workflow_mode": "staged",
            "execution_task": "repair chemistry package",
        }),
        known_verticals=VERTICALS,
        known_domains=BUILTIN_DOMAINS,
        default_execution_task="repair chemistry package",
    )

    assert decision is None


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


def test_a_string_of_earlier_stages_is_not_rendered_letter_by_letter() -> None:
    """`Sequence[str]` accepts a bare string, which iterates as characters.

    A live Manager run on 2026-07-26 was handed a malformed "(none)" and
    reasoned about `(`, `n`, `o`, `n`, `e`, `)` as six rollback targets. It
    caught the nonsense itself, but the prompt should not have been able to say
    it. Production passes a real list; this makes the mistake impossible.
    """
    from types import SimpleNamespace

    from argus_skill.roles.prompts.manager import build_stage_decision_prompt

    review = SimpleNamespace(
        status="done", reason="r", next_action="", operator_question="", checklist=[]
    )
    prompt = build_stage_decision_prompt(
        current_stage="delivery",
        next_stage="",
        earlier_stages="scope",
        checklist_md="- x",
        review=review,
        planner_verdict=None,
        rendering_block="",
        open_ended=True,
        continuous_objective="obj",
    )

    assert "`scope`" in prompt
    assert "`s`, `c`, `o`" not in prompt
