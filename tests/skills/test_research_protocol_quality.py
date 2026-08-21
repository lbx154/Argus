from __future__ import annotations

from pathlib import Path

from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

_SKILLS = (
    Path(__file__).parents[2]
    / "argus_skill"
    / "verticals"
    / "research"
    / "skills"
)
_AMBITION_SKILLS = (
    "engineer/research-ideation.md",
    "engineer/idea-discovery.md",
    "engineer/idea-creator.md",
    "engineer/novelty-check.md",
    "engineer/auto-research-pipeline.md",
    "engineer/research-brief-to-experiment-plan.md",
    "engineer/idea-feasibility-derisk.md",
    "engineer/final-paper-review.md",
    "reviewer/academic-paper-peer-review-benchmark.md",
)


def _skill(relative: str) -> str:
    return (_SKILLS / relative).read_text(encoding="utf-8")


def test_paper_drafting_skills_stay_compact() -> None:
    paths = (
        "engineer/emnlp-paper-drafting.md",
        "engineer/aaai-paper-drafting.md",
        "engineer/research-brief-to-experiment-plan.md",
    )

    sizes = {path: len(_skill(path)) for path in paths}
    assert all(size < 9_000 for size in sizes.values()), sizes
    assert sum(sizes.values()) < 22_000


def test_protocol_runs_positive_recovery_before_accepting_negative_results() -> None:
    results_review = _skill("reviewer/experiment-results-review.md")
    result_to_claim = _skill("engineer/result-to-claim.md")
    analysis = _skill("engineer/research-results-analysis-and-figures.md")
    runner = _skill("engineer/research-experiment-runner.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")

    assert "at most ONE" not in results_review
    assert "write the paper on the current results" not in results_review
    assert "There is no fixed number of optimization passes" in results_review
    assert "independent Reviewer must confirm engineering adequacy" in runner
    assert "no universal requirement to pass every" in runner
    assert "documented methodological reason" in runner
    assert "not automatic write-up" in pipeline
    assert "positive-recovery loop" in result_to_claim
    assert "It need not win on every seed" in result_to_claim
    assert "independent Reviewer" in results_review
    assert "Aim to recover a genuine positive result" in " ".join(pipeline.split())
    assert "chronological experiment report" in analysis
    assert "change labels, discard seeds" not in pipeline
    assert "cherry-pick" not in result_to_claim
    assert "silently selecting favorable" not in results_review


def test_paper_is_claim_driven_and_selective() -> None:
    result_to_claim = _skill("engineer/result-to-claim.md")
    final_review = _skill("engineer/final-paper-review.md")
    analysis = {item.id: item.statement for item in STAGE_CHECKLISTS["analysis"]}
    draft = {item.id: item.statement for item in STAGE_CHECKLISTS["draft"]}

    assert "claim-driven" in result_to_claim
    assert "strongest valid evidence for its thesis" in final_review
    assert "manuscript remains a selective argument" in analysis["analysis.thesis"]
    assert "not a chronological experiment report" in draft["draft.tex"]


def test_publishable_boundary_results_cannot_be_underpowered_pilots() -> None:
    analysis_skill = _skill("engineer/research-results-analysis-and-figures.md")
    peer_review = _skill("reviewer/academic-paper-peer-review-benchmark.md")
    analysis = {item.id: item.statement for item in STAGE_CHECKLISTS["analysis"]}
    plan = {item.id: item.statement for item in STAGE_CHECKLISTS["plan"]}
    review = {item.id: item.statement for item in STAGE_CHECKLISTS["review"]}

    assert "official acceptance" in plan["plan.publication_scale"]
    assert "not universal numeric quotas" in analysis_skill
    assert "pilot_only" in analysis_skill
    assert "claim narrowing" in " ".join(peer_review.lower().split())
    assert "publication-scale evidence" in review["review.publication_value"]
    # The bar is unchanged; the sentence carrying it is not. Stated as a
    # prohibition ("a failed small experiment plus narrower prose is not a
    # contribution") it taught the safest move — claim less — and the drafts
    # read like audit reports. Stated as the standard a boundary finding is
    # held to, it asks for the run instead of the hedge.
    assert (
        "same terms as a positive one"
        in analysis["analysis.publication_scale"]
    )
    assert "at that scale" in analysis["analysis.publication_scale"]


def test_accepted_paper_and_code_organization_is_learned_without_copying() -> None:
    exemplar = _skill("engineer/paper-exemplar-pdf-learning.md")
    peer_review = _skill("reviewer/academic-paper-peer-review-benchmark.md")
    plan = {item.id: item.statement for item in STAGE_CHECKLISTS["plan"]}

    statement = plan["plan.argument_organization"]
    assert "accepted same-area full papers" in statement
    assert "official code" in statement
    assert "Reproduction is not required" in statement
    assert "copying prose" in statement
    assert "ARGUMENT_ORGANIZATION.json" in exemplar
    assert "problem setup" in exemplar
    assert "config/evaluation flow" in exemplar
    assert "without copied prose or a reproduction requirement" in peer_review


def test_live_checklist_requires_thesis_and_implementation_adequacy() -> None:
    run = {item.id: item.statement for item in STAGE_CHECKLISTS["run"]}
    analysis = {item.id: item.statement for item in STAGE_CHECKLISTS["analysis"]}
    draft = {item.id: item.statement for item in STAGE_CHECKLISTS["draft"]}
    review = {item.id: item.statement for item in STAGE_CHECKLISTS["review"]}

    assert "positive-recovery diagnosis loop" in run["run.method_diagnosis_recall"]
    assert "engineering/debugging signal first" in run["run.method_diagnosis_recall"]
    assert "selective argument" in analysis["analysis.thesis"]
    assert "same thesis" in draft["draft.tex"]
    assert "constructive senior coauthor" in review["review.publication_value"]
    assert "Result sign" in review["review.publication_value"]


def test_broad_paper_ideation_uses_eighty_percent_review_quorum() -> None:
    discovery = _skill("engineer/idea-discovery.md")
    creator = _skill("engineer/idea-creator.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    normalized_discovery = " ".join(discovery.split())
    normalized_creator = " ".join(creator.split())
    normalized_pipeline = " ".join(pipeline.split())
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    assert "pool-set --root <team_root> --width 12 --state running" in discovery
    assert "spawn only the missing routes" in discovery
    assert "Never restart a second" in discovery
    assert "A single model call" in discovery
    assert "fresh independent reviewer" in discovery
    assert "`ceil(12 × 0.8) = 10`" in discovery
    assert "advisory feasibility record" in discovery
    assert "at least four routes" in discovery
    assert "network/statistical physics" in normalized_discovery
    assert "12-route team explores concurrently" in research["research.idea_portfolio"]
    assert "80% review quorum" in research["research.idea_portfolio"]
    assert "After at least 80% of reviews" in (
        research["research.adversarial_selection"]
    )
    assert "final routes do not block" in (
        research["research.adversarial_selection"]
    )
    assert "10 of 12 by default" in normalized_creator
    assert "Do not wait for the final two routes" in normalized_creator
    assert "10 of 12 by default" in normalized_pipeline
    assert "final two routes" in normalized_pipeline


def test_research_idea_selection_requires_ambition_without_decorative_math() -> None:
    discovery = _skill("engineer/idea-discovery.md")
    creator = _skill("engineer/idea-creator.md")
    peer_review = _skill("reviewer/academic-paper-peer-review-benchmark.md")
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    assert "Hard technical core" in discovery
    assert "Frontier significance" in discovery
    assert "decorative equations" in discovery
    assert "scaling law" in discovery
    assert "measurable quantities" in discovery
    assert "technical_depth" in creator
    assert "theoretical_foundation" in creator
    thesis = research["research.thesis"]
    assert "plausible nontrivial technical core" in thesis
    assert "formal/causal structure" in thesis
    assert "decorative math" in thesis
    assert "Research review is qualitative" in thesis
    assert "shallow prompt/schema/wrapper/scale" in peer_review


def test_research_selector_prioritizes_frontier_ambition_over_local_ease() -> None:
    discovery = " ".join(_skill("engineer/idea-discovery.md").split())
    creator = " ".join(_skill("engineer/idea-creator.md").split())
    pipeline = " ".join(_skill("engineer/auto-research-pipeline.md").split())
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    for text in (discovery, creator, pipeline):
        assert "high-novelty method" in text
        assert "publication-scale empirical" in text
        assert "shortest evidence path" in text or "shortest-evidence-path" in text
        assert "single-GPU fit" in text
    assert "latest 12 months of arXiv" in discovery
    assert "current major venue cycle" in creator
    assert "resource gaps become an explicit staged compute plan" in (
        research["research.adversarial_selection"]
    )


def test_research_plan_and_run_require_current_generation_backbone() -> None:
    plan = {item.id: item.statement for item in STAGE_CHECKLISTS["plan"]}
    benchmark = {item.id: item.statement for item in STAGE_CHECKLISTS["benchmark"]}
    run = {item.id: item.statement for item in STAGE_CHECKLISTS["run"]}
    runner = _skill("engineer/research-experiment-runner.md")
    results_review = _skill("reviewer/experiment-results-review.md")

    assert "current open model generation" in plan["plan.backbone"]
    assert "live model catalog" in plan["plan.backbone"]
    assert "never the primary publication evidence" in plan["plan.backbone"]
    # The backbone requirement was written into plan, benchmark and run. The
    # middle copy only restated the plan lock, and every restatement is one
    # more box an agent can open a mission to go tick. Planning locks it and
    # the run proves it was executed.
    assert "benchmark.backbone" not in benchmark
    assert "actually execute" in run["run.backbone"]
    assert "plumbing-only" in runner
    assert "cannot become headline evidence by inertia" in runner
    assert "Do not accept it as headline evidence" in " ".join(
        results_review.split()
    )


def test_literature_grounding_advises_ai_and_foundation_balance() -> None:
    discovery = " ".join(_skill("engineer/idea-discovery.md").split())
    pipeline = " ".join(_skill("engineer/auto-research-pipeline.md").split())
    literature = {
        item.id: item.statement for item in STAGE_CHECKLISTS["research"]
    }["research.literature"]

    assert "ACL/EMNLP/NAACL" in discovery
    assert "ICLR/ICML/NeurIPS" in discovery
    assert "AAAI/AAMAS" in discovery
    assert "soft coverage diagnostic, not a quota" in discovery
    assert "AI-venue/recent-arXiv" in pipeline
    assert "AI-venue/recent-arXiv frontier" in literature
    assert "advisory risk" in literature
    assert "not a fixed quota or completion blocker" in literature


def test_research_selection_and_review_skills_share_the_ambition_standard() -> None:
    for path in _AMBITION_SKILLS:
        text = " ".join(_skill(path).split())
        assert "nontrivial technical core" in text, path
        assert "verified originality" in text, path
        assert "formal/causal grounding" in text, path
        assert "field-level consequence" in text, path


def test_manager_and_planner_prompts_preserve_the_ambition_standard() -> None:
    root = Path(__file__).parents[2] / "argus_skill" / "roles" / "prompts"
    manager = (root / "manager.py").read_text(encoding="utf-8")
    planner = (root / "planner.py").read_text(encoding="utf-8")

    for prompt in (manager, planner):
        assert "nontrivial " in prompt and "technical core" in prompt
        assert "verified originality" in prompt
        assert "formal/causal" in prompt
        assert "field-level significance" in prompt


def test_research_smokes_reject_label_leakage_before_model_calls() -> None:
    probe = _skill("engineer/idea-feasibility-derisk.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    plan_review = _skill("reviewer/experiment-plan-review.md")
    benchmark = {
        item.id: item.statement for item in STAGE_CHECKLISTS["benchmark"]
    }

    for text in (probe, pipeline, plan_review):
        assert "gold labels" in text
    assert "remove or permute hidden labels" in probe
    assert "same information and intervention timing" in probe
    assert "one decision-sized milestone" in pipeline
    assert "removing or permuting hidden labels" in (
        benchmark["benchmark.evaluator_authentic"]
    )


def test_research_smokes_record_power_limits_without_rejecting_ideas() -> None:
    probe = _skill("engineer/idea-feasibility-derisk.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    runner = _skill("engineer/research-experiment-runner.md")
    plan_review = _skill("reviewer/experiment-plan-review.md")
    results_review = _skill("reviewer/experiment-results-review.md")
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    for text in (probe, pipeline, runner, plan_review, results_review):
        assert "headroom" in text
        assert "inconclusive" in text
    assert "Research does not decide whether" in research["research.signal_derisk"]
    assert "explicitly skip the probe" in research["research.signal_derisk"]
    assert "cannot kill, block, downgrade, or re-rank" in (
        research["research.signal_derisk"]
    )
    assert "Never reject a qualitatively strong idea" in " ".join(pipeline.split())


def test_route_review_precedes_quorum_selection_and_advisory_probe() -> None:
    creator = _skill("engineer/idea-creator.md")
    probe = _skill("engineer/idea-feasibility-derisk.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    brief = _skill("engineer/research-brief-to-experiment-plan.md")
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}
    generic_planner = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "roles"
        / "prompts"
        / "planner.py"
    ).read_text(encoding="utf-8")
    from argus_skill.verticals.research.stages import role_banner

    assert "Complete this route-local selection" in creator
    assert "Only after Step 1 has selected" in creator
    assert "After an idea has passed method-reasonableness selection" in probe
    assert "selection-before-probe" in pipeline
    assert "earlier dependency" in brief
    assert "Before any probe is designed or executed" in research["research.thesis"]
    assert "thesis may evolve later" in research["research.thesis"]
    assert "After research.thesis admits" in research["research.signal_derisk"]
    normalized_planner = " ".join(role_banner("planner").split())
    assert "At an 80% review quorum" in normalized_planner
    assert "Probe only that winner when" in normalized_planner
    assert "80% review quorum" not in generic_planner
    assert "must not silently change the frozen premise" in creator
    assert "Keep route reviews parallel until at least 80%" in " ".join(
        creator.split()
    )
    assert "final two routes" in " ".join(pipeline.split())


def test_reviewer_treats_research_smokes_as_advisory() -> None:
    generic_verification_policy = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "core"
        / "verification_policy.py"
    ).read_text(encoding="utf-8")
    generic_results_review = _skill("reviewer/experiment-results-review.md")
    from argus_skill.verticals.research.stages import role_banner

    reviewer_banner = role_banner("reviewer")
    assert "Research-stage smoke probes are short advisory observations" in (
        reviewer_banner
    )
    assert "cannot by themselves trigger replan" in reviewer_banner
    assert "smoke is advisory" not in generic_verification_policy
    assert "research-stage smoke probes" not in generic_results_review.lower()


def test_research_prompt_policy_does_not_leak_to_other_verticals() -> None:
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    software = load_vertical("software")

    assert "80% review quorum" in vertical_role_banner(research, "planner")
    assert "Research-stage smoke probes" in vertical_role_banner(
        research, "reviewer"
    )
    for role in ("planner", "engineer", "reviewer"):
        banner = vertical_role_banner(software, role)
        assert "80% review quorum" not in banner
        assert "Research-stage smoke probes" not in banner


def test_dynamic_paper_policy_is_owned_by_research_vertical() -> None:
    generic_root = Path(__file__).parents[2] / "argus_skill" / "roles" / "prompts"
    generic = (
        (generic_root / "planner.py").read_text(encoding="utf-8")
        + (generic_root / "reviewer.py").read_text(encoding="utf-8")
    )
    research = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "verticals"
        / "research"
        / "prompt_policy.py"
    ).read_text(encoding="utf-8")

    for phrase in (
        "Parallel paper-drafting track",
        "Near-complete paper review",
        "Final paper review",
        "PAPER_INFRASTRUCTURE_REVIEW.json",
    ):
        assert phrase in research
        assert phrase not in generic


def test_experiment_review_does_not_repeat_idea_selection() -> None:
    plan_review = _skill("reviewer/experiment-plan-review.md")
    results_review = _skill("reviewer/experiment-results-review.md")

    assert "Do not re-rank its novelty" in plan_review
    assert "not repeating upstream idea selection" in results_review
    assert "Do not re-rank or re-litigate" in results_review
    assert "engineering and protocol validity" in results_review
    assert "decide publication value" in " ".join(results_review.split())
    assert "`pass` means the experiment is engineering-valid" in " ".join(
        results_review.split()
    )
    assert '"idea_status": "untested|inconclusive|supported|refuted"' in results_review
    assert "research/ideas/<id>/EVIDENCE.json" in _skill(
        "engineer/idea-creator.md"
    )


def test_research_protocol_rejects_unsupported_magic_thresholds() -> None:
    brief = _skill("engineer/research-brief-to-experiment-plan.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    plan_review = _skill("reviewer/experiment-plan-review.md")
    results_review = _skill("reviewer/experiment-results-review.md")
    plan = {item.id: item.statement for item in STAGE_CHECKLISTS["plan"]}

    for text in (brief, pipeline, plan_review, results_review):
        assert "round-number" in text
        assert "utility" in text
    assert "unsupported round-number gains" in plan["plan.experiment"]
    assert "continuous evidence" in brief
    assert "cost-quality frontier" in results_review


def test_process_artifacts_are_finishing_steps_not_missions() -> None:
    """Measured on four live ICLR campaigns: 44% of missions were bookkeeping.

    Of 125 completed missions the titles broke down as 55 certification, scope,
    checklist and package missions against 41 that ran an experiment — roughly
    ten hours spent on the harness agreeing with itself. The mechanism is that
    any unsatisfied artifact reads as schedulable work, so process competes with
    science for mission slots and the analysis directories end up named after
    repairs rather than questions.

    Both roles now say the same thing from their own side: the Planner does not
    schedule an artifact as its own mission, and the Reviewer does not return
    work for a missing one.
    """
    from argus_skill.verticals.research.stages import role_banner

    planner = role_banner("planner")
    assert "Each mission advances the argument the paper will make" in planner
    assert "Name missions after the question they answer" in planner
    assert "are not missions of" in planner

    reviewer = role_banner("reviewer")
    assert "belongs in next_action, not in a returned verdict" in reviewer


def test_a_shortfall_is_a_gap_to_close_not_a_verdict() -> None:
    """Every campaign so far shipped a boundary study, because the policy's only
    two endings were a positive result and a write-up of what survived. Falling
    short of the baseline now points at the next fix, the way a leaderboard
    result is earned, and a loss is never what gets written up."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    planner = vertical_role_banner(research, "planner")
    reviewer = vertical_role_banner(research, "reviewer")

    # Selection fixes the number the campaign then spends itself improving.
    for named in ("end-task claim", "resource-matched", "measures the gap"):
        assert named in planner
    assert "close it" in planner

    # A miss buys the next fix; it does not judge the idea.
    assert "leaderboard result is earned" in reviewer
    assert "says nothing about the idea" in reviewer
    assert "a loss is never the paper" in reviewer


def test_probes_still_cannot_veto_a_selected_idea() -> None:
    """A ten-minute smoke test must not be able to stop an idea: only evidence
    at the scale named at selection is what the campaign optimizes against."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    assert "cannot by themselves" in vertical_role_banner(research, "reviewer")
    assert "claim-bearing evidence at " in vertical_role_banner(research, "planner")


def test_only_the_manager_retires_an_idea_and_only_reluctantly() -> None:
    """Grinding the gap down is the campaign's normal state. Deciding an idea is
    dead is one role's call, it is meant to be rare, and impatience is named as
    the thing it will otherwise be mistaken for."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    manager = vertical_role_banner(research, "manager")
    reviewer = vertical_role_banner(research, "reviewer")

    assert "Only you can judge that an idea is genuinely dead" in manager
    assert "rare and expensive" in manager
    assert "impatience" in manager
    # A dead idea returns to selection; it still never becomes a manuscript.
    assert "roll back to selection" in manager
    assert "never becomes is the paper" in manager

    # No other role may make that call.
    assert "the Manager's call" in reviewer
