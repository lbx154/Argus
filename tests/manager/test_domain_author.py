"""Tests for Manager domain authoring parser (``manager/domain_author``)."""
from __future__ import annotations

import json

from argus_skill.manager.domain_author import (
    DomainProposal,
    build_domain_author_prompt,
    parse_domain_proposal,
)
from argus_skill.skills.vertical_select import VERTICALS


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
