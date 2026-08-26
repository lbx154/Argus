"""Tests for Manager domain authoring parser (``manager/domain_author``)."""
from __future__ import annotations

import json

from argus_skill.domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
from argus_skill.manager.domain_author import (
    DomainProposal,
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


def test_parse_accepts_an_already_structured_manager_payload():
    proposal = parse_domain_proposal(
        {
            "name": "robotics_sim",
            "stages": ["scope", "simulate"],
            "rationale": "structured process decision",
        },
        known_verticals=VERTICALS,
    )

    assert proposal is not None
    assert proposal.name == "robotics_sim"
    assert proposal.stages == ["scope", "simulate"]


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


def test_vertical_prompt_keeps_math_routes_inside_builtin_math():
    prompt = build_vertical_decision_prompt(
        "Investigate an open conjecture with literature, computation, proof, and review",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "stable reusable staged capability" in prompt
    assert "Pick the closest existing capability" in prompt
    assert "original mathematical work is `math`" in prompt
    # A ratchet, not a model limit. 56a02152 simplified this prompt and pinned a
    # ceiling so it would not quietly regrow; the number is "a little above
    # whatever it costs today", and it should be raised only for content that
    # earns its space. It moved 7_500 -> 8_100 for the three requirement lines
    # (PRECISE_CONSTRAINTS / EXCLUSIONS / AMBIGUITIES, 544 chars), which carry
    # the operator's own words into the contract. Keep the headroom small.
    assert len(prompt) <= 8_200


def test_vertical_prompts_do_not_treat_one_paper_reading_as_research_pipeline():
    task = "Read this existing paper, explain it, and give me a concise summary."
    grounded = build_vertical_decision_prompt(
        task,
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "This is a read-only routing decision" in grounded


def test_serious_survey_is_staged_without_implied_publication() -> None:
    prompt = build_vertical_decision_prompt(
        "调研 MiniMax-H3 加速部署并形成严肃的中文 PDF survey。",
        verticals_with_purpose=VERTICAL_PURPOSES,
        research_target_verticals=("research",),
    )

    assert "papers and surveys are `research`" in prompt
    assert "publishable only when publication-level original work is requested" in prompt
    assert "Never infer a venue" in prompt


def test_vertical_prompt_composes_chemistry_with_research() -> None:
    prompt = build_vertical_decision_prompt(
        "Run autonomous chemistry research and produce a paper",
        verticals_with_purpose=VERTICAL_PURPOSES,
        domains_with_purpose=DOMAIN_PURPOSES,
    )

    assert "`chemistry`" in prompt
    assert "`domain`" in prompt
    assert "CHOICE=existing" in prompt


def test_vertical_prompt_does_not_escalate_bounded_repo_fix_to_new_domain() -> None:
    prompt = build_vertical_decision_prompt(
        "Repair one failing test in the current repository and return the patch.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "capability VERTICAL" in prompt
    assert "WORKFLOW_MODE=direct" in prompt
    assert "REQUIRE_INDEPENDENT_REVIEW=false" in prompt
    assert "software" in prompt


def test_vertical_parser_preserves_explicit_independent_review() -> None:
    decision = parse_vertical_decision(
        "\n".join([
            "CHOICE=existing",
            "VERTICAL=software",
            "DOMAIN=",
            "WORKFLOW_MODE=direct",
            "REQUIRE_INDEPENDENT_REVIEW=true",
            "RATIONALE=the operator explicitly requested an independent Reviewer",
        ]),
        known_verticals=("software",),
        default_execution_task="Implement and review the cache.",
    )

    assert decision is not None
    assert decision.require_independent_review is True


def test_new_domain_starts_with_real_work_not_process_ceremony() -> None:
    prompt = build_vertical_decision_prompt(
        "Optimize an unfamiliar inference runtime.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "not a one-off task list" in prompt
    assert "action stages" in prompt


def test_vertical_prompt_preserves_explicit_operator_actions() -> None:
    prompt = build_vertical_decision_prompt(
        "Download the BF16 model, quantize it, and run local inference.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "Preserve stated paths, commands, order, and stopping conditions" in prompt
    assert "requested action, not incidental words" in prompt
    assert "Repository work is usually `software`" in prompt


def test_vertical_prompts_do_not_use_software_as_performance_catch_all() -> None:
    task = "Continuously optimize full-model inference serving on eight accelerators."
    grounded = build_vertical_decision_prompt(
        task,
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "Use `new` only when none fits" in grounded
    assert "Pick the closest existing capability" in grounded
    assert "inference/serving" in VERTICAL_PURPOSES["kernel_engineering"]
    assert "inference serving" in grounded
    assert "campaigns are `kernel_engineering`" in grounded


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


def test_fast_vertical_parser_rejects_legacy_direct_alias_with_staged_workflow() -> None:
    route = parse_fast_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "direct",
            "workflow_mode": "staged",
            "confidence": 0.94,
            "rationale": "conflicting alias",
        }),
        known_verticals=VERTICALS,
    )

    assert route is None


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


def test_vertical_parser_defaults_legacy_direct_alias_to_direct_workflow() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "direct",
            "execution_task": "repair the repository",
        }),
        known_verticals=VERTICALS,
    )

    assert decision is not None
    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"


def test_vertical_parser_rejects_legacy_direct_alias_with_staged_workflow() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "direct",
            "workflow_mode": "staged",
            "execution_task": "repair the repository",
        }),
        known_verticals=VERTICALS,
    )

    assert decision is None


def test_vertical_parser_rejects_direct_alias_conflicting_with_persisted_staged() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "direct",
            "execution_task": "repair the repository",
        }),
        known_verticals=VERTICALS,
        persisted_vertical="software",
        persisted_workflow_mode="staged",
    )

    assert decision is None


def test_vertical_parser_recovers_direct_mode_from_legacy_persisted_alias() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "software",
            "execution_task": "repair the repository",
        }),
        known_verticals=VERTICALS,
        persisted_vertical="direct",
    )

    assert decision is not None
    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"


def test_new_operator_handoff_can_raise_same_research_route_contract() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "research_target_level": "publishable",
            "research_direction_mode": "broad",
            "execution_task": "Run real experiments for a submission-grade result.",
        }),
        known_verticals=VERTICALS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="direct",
        persisted_research_target_level="exploratory",
        persisted_research_direction_mode="locked",
        allow_persisted_change=True,
    )

    assert decision is not None
    assert decision.workflow_mode == "staged"
    assert decision.research_target_level == "publishable"
    assert decision.research_direction_mode == "broad"


def test_new_handoff_recovers_prior_direction_from_noncontract_model_label() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "research_target_level": "exploratory",
            "research_direction_mode": "experimental_validation",
            "execution_task": "Run a bounded real-model smoke test.",
        }),
        known_verticals=VERTICALS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="direct",
        persisted_research_target_level="exploratory",
        persisted_research_direction_mode="locked",
        allow_persisted_change=True,
    )

    assert decision is not None
    assert decision.workflow_mode == "staged"
    assert decision.research_direction_mode == "locked"


def test_vertical_parser_recovers_required_persisted_research_target() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "execution_task": "continue the paper",
        }),
        known_verticals=VERTICALS,
        known_domains=BUILTIN_DOMAINS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="staged",
        persisted_domain="chemistry",
        persisted_research_target_level="publishable",
    )

    assert decision is not None
    assert decision.domain == "chemistry"
    assert decision.research_target_level == "publishable"
    assert decision.research_direction_mode == "broad"


def test_vertical_parser_preserves_operator_locked_research_hypothesis() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "research_target_level": "publishable",
            "research_direction_mode": "locked",
            "execution_task": "test the operator's fixed hypothesis",
        }),
        known_verticals=VERTICALS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="staged",
        persisted_research_target_level="publishable",
        persisted_research_direction_mode="locked",
    )

    assert decision is not None
    assert decision.research_direction_mode == "locked"


def test_vertical_parser_does_not_trust_fresh_model_locked_claim() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "research_target_level": "publishable",
            "research_direction_mode": "locked",
            "execution_task": "find a paper idea",
        }),
        known_verticals=VERTICALS,
        research_target_verticals=("research",),
    )

    assert decision is not None
    assert decision.research_direction_mode == "broad"


def test_vertical_parser_rejects_downgrading_persisted_broad_research() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "workflow_mode": "staged",
            "research_target_level": "publishable",
            "research_direction_mode": "locked",
            "execution_task": "continue the paper",
        }),
        known_verticals=VERTICALS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="staged",
        persisted_research_target_level="publishable",
        persisted_research_direction_mode="broad",
    )

    assert decision is None


def test_vertical_parser_rejects_changed_persisted_research_domain() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "research",
            "domain": "physics",
            "workflow_mode": "staged",
            "execution_task": "continue the paper",
        }),
        known_verticals=VERTICALS,
        known_domains=BUILTIN_DOMAINS,
        research_target_verticals=("research",),
        persisted_vertical="research",
        persisted_workflow_mode="staged",
        persisted_domain="chemistry",
        persisted_research_target_level="publishable",
    )

    assert decision is None


def test_fast_vertical_parser_rejects_explicit_workflow_conflict_with_persisted() -> None:
    route = parse_fast_vertical_decision(
        json.dumps({
            "choice": "existing",
            "name": "software",
            "workflow_mode": "direct",
            "confidence": 0.94,
            "rationale": "conflicting persisted identity",
        }),
        known_verticals=VERTICALS,
        persisted_vertical="software",
        persisted_workflow_mode="staged",
    )

    assert route is None


def test_vertical_parser_accepts_in_place_data_domain_adaptation() -> None:
    decision = parse_vertical_decision(
        json.dumps({
            "choice": "existing",
            "vertical": "regulated_localization",
            "stages": [
                "terminology_lock",
                "translation",
                "regulatory_review",
                "layout_qa",
                "linguistic_qa",
                "release",
            ],
            "workflow_mode": "staged",
            "execution_task": "Localize and release the regulated product UI.",
            "rationale": "the matching one-stage domain is materially underfit",
        }),
        known_verticals=VERTICALS,
        existing_data_domains=["regulated_localization"],
    )

    assert decision is not None
    assert decision.choice == "existing"
    assert decision.vertical == "regulated_localization"
    assert decision.adapted_stages == (
        "terminology_lock",
        "translation",
        "regulatory_review",
        "layout_qa",
        "linguistic_qa",
        "release",
    )


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


def test_grounded_vertical_prompt_preserves_manager_agency_and_planner_boundary() -> None:
    prompt = build_vertical_decision_prompt(
        "Build a novel controller whose repository structure is unknown.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "inspect only when the fit is unclear" in prompt
    assert "no task work or Live View" in prompt
    assert "presentations" not in prompt
    assert "Omit `execution_task` for a standalone existing route" in prompt
    assert "include it only when bounded context must be rewritten" in prompt


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

    from argus_skill.roles.prompts.manager import build_stage_decision_prompt

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
