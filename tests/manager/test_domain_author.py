"""The route form's check (``RouteContext``), domain proposals and the routing prompts."""
from __future__ import annotations

import pytest

from argus.domains import BUILTIN_DOMAINS
from argus.manager.domain_author import (
    DomainProposal,
    RouteContext,
    parse_domain_proposal,
)
from argus.manager.form import manager_actions
from argus.skills.vertical_select import VERTICALS
from argus.verticals._data_domain import CANDIDATE_DOMAIN_STAGES
from tests.hand_off import ticket

RESEARCH = RouteContext(
    known_verticals=VERTICALS, known_domains=BUILTIN_DOMAINS, research_target_verticals=("research",),
)
PERSISTED = RouteContext(
    known_verticals=VERTICALS, known_domains=(*BUILTIN_DOMAINS, "physics"), research_target_verticals=("research",),
    persisted_vertical="research", persisted_workflow_mode="staged", persisted_domain="chemistry",
    persisted_research_target_level="publishable", persisted_research_direction_mode="broad",
)


def _route(context: RouteContext = RouteContext(known_verticals=VERTICALS), **fields):
    """The route a Manager ticket with ``fields`` is checked to (``Manager.adopt_route``)."""
    return context.decide({"choice": "existing", "summary": "why this route", "assumptions": [],
                           "execution_task": "do the task", **fields})


def _refused(context: RouteContext = RouteContext(known_verticals=VERTICALS), **fields) -> str:
    with pytest.raises(ValueError) as refusal:
        _route(context, **fields)
    return str(refusal.value)


def test_parse_domain_proposal_uses_runtime_owned_stages():
    p = parse_domain_proposal(
        {"name": "robotics_sim", "stages": ["scope", "simulate"], "rationale": "novel control domain"},
        known_verticals=VERTICALS,
    )
    assert isinstance(p, DomainProposal)
    assert p.name == "robotics_sim"
    assert p.stages == list(CANDIDATE_DOMAIN_STAGES)


def test_parse_sluggifies_name_and_needs_one():
    assert parse_domain_proposal({"name": "Robotics Sim!"}, known_verticals=VERTICALS).name == "robotics_sim"
    assert parse_domain_proposal({}, known_verticals=VERTICALS) is None


def test_parse_dedupes_name_against_known_and_existing():
    # Collision with a preset vertical → suffixed, not rejected.
    p = parse_domain_proposal({"name": "research"}, known_verticals=VERTICALS, existing_data_domains=["research_2"])
    assert p is not None and p.name not in ("research", "research_2")


















@pytest.mark.parametrize(
    ("vertical", "workflow_mode", "start_stage", "expected"),
    [
        ("research", "direct", "paper", "paper"),
        ("research", "staged", "paper", ""),
        ("research", "direct", "  DrAfT  ", "paper"),
        ("research", "direct", "not_a_stage", ""),
        ("research", "direct", "", ""),
        ("research", "direct", "idea", "idea"),
        ("software", "direct", "paper", ""),
    ],
)
def test_start_stage_counts_only_in_a_direct_vertical(vertical, workflow_mode, start_stage, expected) -> None:
    context = RouteContext(known_verticals=VERTICALS, research_target_verticals=("research",))
    decision = _route(
        context, vertical=vertical, workflow_mode=workflow_mode, start_stage=start_stage,
        research_target_level="exploratory", research_direction_mode="locked",
    )
    assert decision.start_stage == expected


def test_a_contextual_task_needs_its_own_execution_task() -> None:
    assert "execution_task" in _refused(vertical="software", execution_task="")
    assert _route(RouteContext(known_verticals=VERTICALS, default_execution_task="repair"),
                  vertical="software", execution_task="").execution_task == "repair"


def test_a_vertical_must_be_on_the_menu() -> None:
    assert "vertical must be one of" in _refused(vertical="astrology")


def test_research_takes_a_listed_domain_and_nothing_else_does() -> None:
    decision = _route(RESEARCH, vertical="research", domain="chemistry", research_target_level="publishable")
    assert (decision.domain, decision.research_direction_mode) == ("chemistry", "broad")
    assert "optional research domain" in _refused(RESEARCH, vertical="software", domain="chemistry")
    assert "domain must be empty or one of" in _refused(
        RESEARCH, vertical="research", domain="astrology", research_target_level="publishable",
    )


def test_the_legacy_direct_alias_is_software_run_directly() -> None:
    decision = _route(vertical="direct")
    assert (decision.vertical, decision.workflow_mode) == ("software", "direct")
    assert "workflow_mode=direct" in _refused(vertical="direct", workflow_mode="staged")
    recovered = _route(RouteContext(known_verticals=VERTICALS, persisted_vertical="direct"), vertical="software")
    assert recovered.workflow_mode == "direct"


def test_a_research_target_is_required_or_inherited() -> None:
    assert "research_target_level is required" in _refused(RESEARCH, vertical="research")
    decision = _route(PERSISTED, vertical="research", workflow_mode="staged")
    assert (decision.domain, decision.research_target_level, decision.research_direction_mode) == (
        "chemistry", "publishable", "broad",
    )
    fresh = _route(RESEARCH, vertical="research", research_target_level="publishable", research_direction_mode="locked")
    assert fresh.research_direction_mode == "locked"


@pytest.mark.parametrize(
    "fields",
    [
        {"workflow_mode": "direct"},
        {"domain": "physics"},
        {"research_target_level": "exploratory"},
        {"research_direction_mode": "locked"},
    ],
)
def test_supplemental_work_keeps_the_persisted_route_contract(fields) -> None:
    refusal = _refused(PERSISTED, vertical="research", **fields)
    assert "the active route contract fixes it" in refusal
    reopened = RouteContext(**{**PERSISTED.__dict__, "allow_persisted_change": True})
    decision = _route(reopened, vertical="research", **fields)
    assert all(getattr(decision, key) == value for key, value in fields.items())


def test_a_direction_mode_outside_the_form_is_refused() -> None:
    actions = manager_actions("write the paper", active_mission=False)
    with pytest.raises(ValueError, match="research_direction_mode"):
        actions.dispatch(*ticket("Write the paper.", vertical="research", research_target_level="exploratory",
                                 research_direction_mode="experimental_validation"))


def test_a_new_domain_takes_no_profile_and_needs_a_slug() -> None:
    decision = _route(choice="new", vertical="Robotics Sim", workflow_mode="direct", start_stage="frame")
    assert (decision.vertical, decision.proposal.rationale) == ("robotics_sim", "why this route")
    assert "no workflow profiles" in _refused(choice="new", vertical="robotics_sim", workflow_profile="full")
    assert "slug" in _refused(choice="new", vertical="!!")










def test_a_string_of_earlier_stages_is_not_rendered_letter_by_letter() -> None:
    """`Sequence[str]` accepts a bare string, which iterates as characters.

    A live Manager run on 2026-07-26 was handed a malformed "(none)" and
    reasoned about `(`, `n`, `o`, `n`, `e`, `)` as six rollback targets. It
    caught the nonsense itself, but the prompt should not have been able to say
    it. Production passes a real list; this makes the mistake impossible.
    """
    from types import SimpleNamespace

    from argus.roles.prompts.manager import build_stage_decision_prompt

    review = SimpleNamespace(
        status="done", reason="r", next_action="", operator_question="", checklist=[]
    )
    prompt = build_stage_decision_prompt(
        current_stage="delivery",
        next_stage="",
        later_stages=[],
        earlier_stages="scope",
        checklist_md="- x",
        review=review,
        planner_verdict=None,
        open_ended=True,
        continuous_objective="obj",
    )

    assert "`scope`" in prompt
    assert "`s`, `c`, `o`" not in prompt
