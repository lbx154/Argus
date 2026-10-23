"""The route form's check (``RouteContext``), domain proposals and the routing prompts."""
from __future__ import annotations

import pytest

from argus.core.handoff import HandOffActions
from argus.domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
from argus.manager.domain_author import (
    DomainProposal,
    RouteContext,
    build_vertical_decision_prompt,
    parse_domain_proposal,
)
from argus.skills.vertical_select import VERTICAL_PURPOSES, VERTICALS
from argus.verticals._data_domain import CANDIDATE_DOMAIN_STAGES

RESEARCH = RouteContext(
    known_verticals=VERTICALS, known_domains=BUILTIN_DOMAINS, research_target_verticals=("research",),
)
PERSISTED = RouteContext(
    known_verticals=VERTICALS, known_domains=(*BUILTIN_DOMAINS, "physics"), research_target_verticals=("research",),
    persisted_vertical="research", persisted_workflow_mode="staged", persisted_domain="chemistry",
    persisted_research_target_level="publishable", persisted_research_direction_mode="broad",
)


def _route(context: RouteContext = RouteContext(known_verticals=VERTICALS), **fields):
    """What the route form records for a hand_off call with ``fields``."""
    actions = HandOffActions("route", check=context.decide, operator_available=True)
    actions.dispatch("hand_off", {
        "to": "team", "summary": "why this route", "assumptions": [], "choice": "existing",
        "execution_task": "do the task", **fields,
    })
    return actions.handoff.value


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


def test_vertical_prompt_stays_compact():
    prompt = build_vertical_decision_prompt(
        "Investigate an open conjecture with literature, computation, proof, and review",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "stable reusable staged capability" in prompt
    assert "Pick the closest existing capability" in prompt
    # A ratchet, not a model limit: "a little above whatever it costs today",
    # raised only for content that earns its space. The field documentation
    # lives in the route form, not here.
    assert len(prompt) <= 5_300


def test_vertical_prompts_do_not_treat_one_paper_reading_as_research_pipeline():
    task = "Read this existing paper, explain it, and give me a concise summary."
    grounded = build_vertical_decision_prompt(
        task,
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert 'Decide by reading only' in grounded


def test_serious_survey_is_staged_without_implied_publication() -> None:
    prompt = build_vertical_decision_prompt(
        "调研 MiniMax-H3 加速部署并形成严肃的中文 PDF survey。",
        verticals_with_purpose=VERTICAL_PURPOSES,
        research_target_verticals=("research",),
    )

    assert "papers and surveys are `research`" in prompt
    assert 'publishable for requested original work at publication level' in prompt
    assert 'add `research_target_level`' in prompt
    assert "`research_direction_mode`" in prompt
    assert "Never infer a venue" in prompt


def test_research_routing_distinguishes_idea_only_from_paper_production() -> None:
    prompt = build_vertical_decision_prompt(
        "Use Argus to propose one strong idea.",
        verticals_with_purpose=VERTICAL_PURPOSES,
        research_target_verticals=("research",),
    )

    assert 'supplied idea or hypothesis requested as a full paper' in prompt
    assert "choose staged research with direction locked" in prompt
    assert 'For proposing, comparing, or reviewing ideas' in prompt
    assert "choose direct research" in prompt
    assert 'never expands an idea-only request into a paper' in prompt
    assert 'Research figures, plots, diagrams, Figure 1' in prompt
    assert 'manuscript revisions are `research`' in prompt
    assert 'produce that part without adding a manuscript, experiments, or literature review' in prompt
    assert "Optional start_stage applies only to workflow_mode=direct" in prompt
    assert '`paper` for figures, drafts, sections, or revisions' in prompt


def test_vertical_prompt_composes_chemistry_with_research() -> None:
    prompt = build_vertical_decision_prompt(
        "Run autonomous chemistry research and produce a paper",
        verticals_with_purpose=VERTICAL_PURPOSES,
        domains_with_purpose=DOMAIN_PURPOSES,
    )

    assert "`chemistry`" in prompt
    assert "`domain`" in prompt
    assert "End with hand_off" in prompt


def test_vertical_prompt_does_not_escalate_bounded_repo_fix_to_new_domain() -> None:
    prompt = build_vertical_decision_prompt(
        "Repair one failing test in the current repository and return the patch.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert 'Choose VERTICAL' in prompt
    assert "`direct` for one coherent Engineer task" in prompt
    assert 'Independent review defaults on' in prompt
    assert "software" in prompt


def test_new_domain_starts_with_real_work_not_process_ceremony() -> None:
    prompt = build_vertical_decision_prompt(
        "Optimize an unfamiliar inference runtime.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert 'Host manages candidate development' in prompt
    assert "do not propose or revise stage names" in prompt
    assert "STAGES" not in prompt


def test_vertical_prompt_preserves_explicit_operator_actions() -> None:
    prompt = build_vertical_decision_prompt(
        "Download the BF16 model, quantize it, and run local inference.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert 'Preserve paths, commands, order, and stopping conditions' in prompt
    assert 'requested action, not words' in prompt
    assert "Repository work is usually `software`" in prompt
    # 2026-10-20: "经独立审阅后交付" in the Engineer's task had it run its own
    # review before the Reviewer ran the formal one.
    assert "leave review out, since `require_independent_review` carries it" in prompt


@pytest.mark.parametrize("fast", [True, False])
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
def test_start_stage_counts_only_in_a_direct_vertical(fast, vertical, workflow_mode, start_stage, expected) -> None:
    context = RouteContext(known_verticals=VERTICALS, research_target_verticals=("research",), fast=fast)
    decision = _route(
        context, vertical=vertical, workflow_mode=workflow_mode, start_stage=start_stage,
        research_target_level="exploratory", research_direction_mode="locked",
    )
    assert decision.start_stage == expected


@pytest.mark.parametrize("choice", ["grounded", "new"])
def test_the_fast_pass_commits_only_an_existing_route(choice) -> None:
    assert _route(RouteContext(known_verticals=VERTICALS, fast=True), choice=choice, vertical="x_y") is None
    assert "fast pass" in _refused(choice="grounded")


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
    assert "research_direction_mode" in _refused(
        RESEARCH, vertical="research", research_target_level="exploratory",
        research_direction_mode="experimental_validation",
    )


def test_a_new_domain_takes_no_profile_and_needs_a_slug() -> None:
    decision = _route(choice="new", vertical="Robotics Sim", workflow_mode="direct", start_stage="frame")
    assert (decision.vertical, decision.proposal.rationale) == ("robotics_sim", "why this route")
    assert "no workflow profiles" in _refused(choice="new", vertical="robotics_sim", workflow_profile="full")
    assert "slug" in _refused(choice="new", vertical="!!")


def test_grounded_vertical_prompt_preserves_manager_agency_and_planner_boundary() -> None:
    prompt = build_vertical_decision_prompt(
        "Build a novel controller whose repository structure is unknown.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert 'inspect if the fit is unclear' in prompt
    assert "no task work or Live View" in prompt
    assert "presentations" not in prompt
    assert 'omit it for a standalone existing route' in prompt
    # A Task with conversation context needs the handoff on the first try.
    assert "or when the Task carries conversation context" in prompt


def test_read_only_repository_audit_avoids_maintenance_meta_review() -> None:
    prompt = build_vertical_decision_prompt(
        "Audit this repository and produce one report without changing runtime code.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "Repository work is usually `software`" in prompt
    assert "Argus runtime changes are `argus_maintenance`" in prompt


def test_paper_process_audit_routes_by_deliverable_not_argus_noun() -> None:
    prompt = build_vertical_decision_prompt(
        "Use Argus to generate an ICLR paper and audit the paper-generation process.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "papers and surveys are `research`" in prompt
    assert "Argus runtime changes are `argus_maintenance`" in prompt


def test_vertical_prompts_prefer_matching_formal_project_domain() -> None:
    kwargs = {
        "verticals_with_purpose": VERTICAL_PURPOSES,
        "existing_data_domains": ["apple_mlx_inference"],
        "existing_data_domain_summaries": {
            "apple_mlx_inference": (
                "status=formal; Apple Silicon MLX/Metal deployment and inference optimization"
            )
        },
    }

    grounded = build_vertical_decision_prompt("Optimize MiniMax H3 on M4 Pro", **kwargs)

    assert "status=formal" in grounded
    assert "Apple Silicon MLX/Metal deployment" in grounded
    assert "Prefer a matching formal project domain" in grounded
    assert "put its exact slug in `vertical`" in grounded
    assert "leave `domain` empty" in grounded


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
