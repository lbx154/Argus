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
_BUILTIN_SKILLS = Path(__file__).parents[2] / "argus_skill" / "builtin_skills"
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


def _builtin_skill(relative: str) -> str:
    return (_BUILTIN_SKILLS / relative).read_text(encoding="utf-8")


def test_method_faithfulness_is_one_draft_stage_research_obligation() -> None:
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    faithfulness_ids = [
        item.id
        for checklist in STAGE_CHECKLISTS.values()
        for item in checklist
        if "method_faithfulness" in item.id
    ]
    assert faithfulness_ids == ["draft.research.method_faithfulness"]

    item = next(
        item
        for item in STAGE_CHECKLISTS["draft"]
        if item.id == "draft.research.method_faithfulness"
    )
    normalized = " ".join(item.statement.split())
    assert "training/eval" in normalized
    assert "file:line" in normalized
    assert "record the trace in `paper/CLAIM_TO_CODE_TRACE.md`" in normalized
    assert "implement the claimed mechanism" in normalized
    assert "rewrite the paper to describe what the code actually does" in normalized
    assert "independently certify" not in normalized.lower()
    assert "evidence chain" not in normalized.lower()

    planner = " ".join(_PLANNER_RESEARCH_ORCHESTRATION.split())
    assert "Before the writing stage begins" in planner
    assert "schedule one claim-to-code-trace mission" in planner
    assert "executed file:line anchors" in planner


def test_claim_to_code_trace_skill_follows_executed_quantities() -> None:
    skill = _builtin_skill("reviewer/claim-to-code-trace.md")

    for verdict in ("MATCHES", "CONTRADICTS", "NOT-IMPLEMENTED"):
        assert verdict in skill
    assert "follow the actual call chain" in skill.lower()
    assert "Compare formulas symbol by symbol" in skill
    assert "branch_prefix_hash" in skill
    assert "only hashes" in skill
    assert "whole completions" in skill
    assert "suffix_logprob(model, prompt, completion)" in skill
    assert "Names lie; call graphs and operands do not" in " ".join(skill.split())


def test_repeats_buy_sampling_information_not_assurance() -> None:
    plan_review = _skill("reviewer/experiment-plan-review.md")
    results_review = _skill("reviewer/experiment-results-review.md")
    audit = _skill("reviewer/experiment-audit.md")
    runner = _skill("engineer/research-experiment-runner.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")
    texts = (plan_review, results_review, audit, runner, pipeline)

    for text in texts:
        normalized = " ".join(text.split())
        assert "sampling noise and nothing else" in normalized
        assert "noise floor" in normalized
        assert "positive control" in normalized or "positive-control" in normalized
        assert "claim-code mismatch" in normalized
    assert "Ritual repetition is a defect, not diligence" in audit
    assert "one run suffices" in " ".join(runner.split())


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
    # The bar is unchanged; the sentence carrying it has now failed twice.
    # Stated as a prohibition ("a failed small experiment plus narrower prose
    # is not a contribution") it taught the safest move — claim less — and the
    # drafts read like audit reports. Stated as the standard a boundary finding
    # is held to ("earns the paper on exactly the same terms as a positive
    # one") it read as permission: an observation campaign shipped a method
    # scoring 0.792 against its own 0.812 comparator with the deficit
    # propagated into five artifacts, and no stage asked for another round.
    # Both wordings only ruled on how to DESCRIBE a result. This one puts the
    # shortfall back in the work queue, which is what `submission.result_stands`
    # already does one stage later and what analysis was missing.
    assert (
        "the next experiment"
        in analysis["analysis.publication_scale"]
    )
    assert (
        "narrower prose does not close it"
        in analysis["analysis.publication_scale"]
    )


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


def test_broad_paper_ideation_uses_judged_breadth_not_a_quorum() -> None:
    discovery = _skill("engineer/idea-discovery.md")
    creator = _skill("engineer/idea-creator.md")
    normalized_creator = " ".join(creator.split())
    pipeline = _skill("engineer/auto-research-pipeline.md")
    normalized_discovery = " ".join(discovery.split())
    normalized_creator = " ".join(creator.split())
    normalized_pipeline = " ".join(pipeline.split())
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    assert "genuinely distinct mechanism families" in discovery
    assert "twelve-route fanout is a useful default example, not a quota" in normalized_discovery
    assert "number of tasks or provider width" in normalized_discovery
    assert "twelve-route fanout is an operating example, not a breadth quota" in (
        research["research.idea_portfolio"]
    )
    assert "judges when the evidence covers" in research["research.adversarial_selection"]
    assert "including probes and later routes" in research["research.adversarial_selection"]
    assert "rather than satisfying a route count" in normalized_creator
    assert "breadth and selection sufficiency remain Agent judgments" not in normalized_creator
    assert "fresh selector judges when" in normalized_pipeline
    for text in (normalized_discovery, normalized_creator, normalized_pipeline):
        assert "80%" not in text
        assert "10 of 12" not in text


def test_research_idea_selection_requires_ambition_without_decorative_math() -> None:
    discovery = _skill("engineer/idea-discovery.md")
    creator = _skill("engineer/idea-creator.md")
    normalized_creator = " ".join(creator.split())
    peer_review = _skill("reviewer/academic-paper-peer-review-benchmark.md")
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    assert "Hard technical core" in discovery
    assert "Frontier significance" in discovery
    assert "decorative equations" in discovery
    assert "scaling law" in discovery
    assert "measurable quantities" in discovery
    assert "important, credible, nontrivial new knowledge" in normalized_creator
    assert "natural language rather than manufacturing scores" in normalized_creator
    thesis = research["research.thesis"]
    assert "plausible nontrivial technical core" in thesis
    assert "formal/causal structure" in thesis
    assert "decorative math" in thesis
    assert "Research review is qualitative" in thesis
    assert "shallow prompt/schema/wrapper/scale" in peer_review


def test_research_selector_judges_any_contribution_form_over_local_ease() -> None:
    discovery = " ".join(_skill("engineer/idea-discovery.md").split())
    creator = " ".join(_skill("engineer/idea-creator.md").split())
    pipeline = " ".join(_skill("engineer/auto-research-pipeline.md").split())
    research = {item.id: item.statement for item in STAGE_CHECKLISTS["research"]}

    assert "Theory, measurement, datasets, methods, anomalies, negative results" in discovery
    assert "theory result, measurement, dataset, method, anomaly" in creator
    assert "any contribution form" in pipeline
    for text in (creator, pipeline):
        assert "local convenience" in text
    assert "local convenience is not scientific value" in (
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

    assert "primary papers and official artifacts" in discovery
    assert "Search mathematical, physical, statistical" in pipeline
    assert "AI-venue/recent-arXiv" in pipeline
    assert "AI-venue/recent-arXiv frontier" in literature
    assert "advisory risk" in literature
    assert "not a fixed quota or completion blocker" in literature


def test_research_selection_and_review_skills_share_the_ambition_standard() -> None:
    for path in _AMBITION_SKILLS:
        text = " ".join(_skill(path).split())
        if path == "engineer/idea-creator.md":
            assert "important, credible, nontrivial new knowledge" in text
            continue
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
    assert "relevance the Reviewer must judge" in (
        research["research.signal_derisk"]
    )
    assert "Never reject a qualitatively strong idea" in " ".join(pipeline.split())


def test_route_review_precedes_judged_selection_and_uses_probe_evidence() -> None:
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

    normalized_creator = " ".join(creator.split())
    assert "Review each route as it arrives" in normalized_creator
    assert "Use probes as evidence" in creator
    assert "After an idea has passed method-reasonableness selection" in probe
    assert "selection-before-probe" in pipeline
    assert "earlier dependency" in brief
    assert "Before any probe is designed or executed" in research["research.thesis"]
    assert "thesis may evolve later" in research["research.thesis"]
    assert "After research.thesis admits" in research["research.signal_derisk"]
    normalized_planner = " ".join(role_banner("planner").split())
    assert "independent selector" in normalized_planner
    assert "without waiting for every late route" in normalized_planner
    assert "Use early probes only when" in normalized_planner
    assert "80% review quorum" not in generic_planner
    assert "Let credible later evidence reopen the comparison" in normalized_creator
    assert "80%" not in creator
    assert "all evidence that has arrived" in " ".join(pipeline.split())


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
    assert "could scale/setup show the effect if it existed" in reviewer_banner
    assert "this idea has not yet been given a real chance" in reviewer_banner
    assert "smoke is advisory" not in generic_verification_policy
    assert "research-stage smoke probes" not in generic_results_review.lower()


def test_research_prompt_policy_does_not_leak_to_other_verticals() -> None:
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    software = load_vertical("software")

    assert "independent selector" in vertical_role_banner(research, "planner")
    assert "scale/setup show the effect" in vertical_role_banner(
        research, "reviewer"
    )
    for role in ("planner", "engineer", "reviewer"):
        banner = vertical_role_banner(software, role)
        assert "independent selector" not in banner
        assert "scale/setup show the effect" not in banner


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
    assert "Each mission must advance the paper's argument" in planner
    assert "not standalone certification, schema, or bookkeeping" in planner

def test_a_shortfall_is_attributed_by_discriminating_evidence() -> None:
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    planner = vertical_role_banner(research, "planner")
    reviewer = vertical_role_banner(research, "reviewer")

    # Selection fixes the number the campaign then spends itself improving.
    for named in ("end claim", "strongest relevant", "skeptical reader"):
        assert named in planner
    assert "claim-bearing evidence" in planner

    assert "well-characterized negative result, anomaly, or boundary condition" in reviewer
    for question in (
        "executed call chain faithful to the idea",
        "baselines/hyperparameters get competent, competitive effort",
        "scale/setup show the effect if it existed",
        "credible alternative explanations excluded",
    ):
        assert question in reviewer
    assert "this idea has not yet been given a real chance" in reviewer
    assert "unfinished work, not a negative result, paper section" in reviewer
    assert "a loss is never the paper" not in reviewer

    results_review = _skill("reviewer/experiment-results-review.md")
    peer_review = _skill("reviewer/academic-paper-peer-review-benchmark.md")
    for surface in (results_review, peer_review):
        normalized = " ".join(surface.split())
        assert "Before accepting any negative conclusion" in normalized
        assert "executed call chain" in normalized
        assert "baselines and hyperparameters" in normalized
        assert "scale and setup" in normalized
        assert "credible alternative explanations" in normalized
        assert "this idea has not yet been given a real chance" in normalized


def test_paper_review_deletes_unanchored_humility_and_virtue_signaling() -> None:
    from argus_skill.verticals.research.prompt_policy import academic_paper_review_block

    surfaces = (
        _skill("reviewer/aaai-academic-language-review.md"),
        _skill("reviewer/emnlp-academic-language-review.md"),
        _skill("reviewer/academic-paper-peer-review-benchmark.md"),
        academic_paper_review_block(),
    )
    for surface in surfaces:
        for label in ("bounded", "limited", "preliminary", "受限"):
            assert label in surface
        assert "unsupported humility" in surface
        assert "named, concrete limitation with evidence" in surface
        assert "limitations that would change a reader's decision" in surface
        assert "virtue-signaling filler or integrity self-praise" in surface


def test_probes_still_cannot_veto_a_selected_idea() -> None:
    """A ten-minute smoke test must not be able to stop an idea: only evidence
    at the scale named at selection is what the campaign optimizes against."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    reviewer = vertical_role_banner(research, "reviewer")
    assert "could scale/setup show the effect if it existed" in reviewer
    assert "this idea has not yet been given a real chance" in reviewer
    assert "claim-bearing evidence at " in vertical_role_banner(research, "planner")


def test_research_reviewer_adjudicates_progress_against_living_plan(tmp_path) -> None:
    from argus_skill.reviewer import Reviewer
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    reviewer = Reviewer(runner=None, skill_store=None)._build_prompt(
        objective="run the decisive experiment",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="experiment completed",
        main_error=None,
        prior_checkpoint={},
        working_dir=tmp_path,
    )

    assert (
        "Judge whether the mission advanced the research plan's stated program, "
        "not merely whether it completed its own scope."
    ) in reviewer


def test_only_the_manager_retires_an_idea_and_only_reluctantly() -> None:
    """Grinding the gap down is the campaign's normal state. Deciding an idea is
    dead is one role's call, it is meant to be rare, and impatience is named as
    the thing it will otherwise be mistaken for."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    research = load_vertical("research")
    manager = vertical_role_banner(research, "manager")
    reviewer = vertical_role_banner(research, "reviewer")

    assert "Retire only when trustworthy evidence" in manager
    assert "materially different attempts" in manager
    assert "roll the accumulated learning into a stronger direction" in manager
    assert "same evidence quality as promoting one" in manager
    assert "a faithful executed call chain" in manager
    assert "competent competitive baselines and hyperparameters" in manager
    assert "a setup and scale able to reveal the effect" in manager
    assert "exclusion of credible alternative explanations" in manager
    assert "defer the route as `not yet given a real chance`" in manager

    # No other role may make that call.
    assert "Manager-owned retirement" in reviewer
    assert "same claim" in reviewer
    assert "means `not yet given a real chance`, not retired" in reviewer


def test_the_grind_skill_says_what_a_campaign_does_between_rounds() -> None:
    """Argus grinds well on its own infrastructure and stops after two rounds on
    the science. The skill exists to carry that appetite across, and the roles
    that would need it have to be able to find it."""
    from argus_skill.skills.builtins import iter_vertical_skill_texts
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    skill = dict(iter_vertical_skill_texts("research"))["engineer/research-grind.md"]

    # A first miss is a draft, not a verdict, and the causes are enumerated.
    assert "first implementation is a first draft" in skill
    for cause in ("implementation", "optimizer", "data slice", "scale", "evaluator"):
        assert cause in skill

    # Flat stretches are the middle of the problem, not a signal.
    assert "Troughs are part of the shape" in skill
    assert "lowering the target" in skill

    # The method mutates while it is ground, and the paper follows the method.
    assert "not the method you started" in skill
    assert "That is the research happening." in skill

    # Judgement over procedure.
    assert "None of this is a procedure to execute" in skill
    assert "Follow the surprising thing" in skill

    research = load_vertical("research")
    for role in ("engineer", "manager"):
        banner = vertical_role_banner(research, role)
        assert "shortfall" in banner or "first result" in banner
        assert "measure" in banner


def test_the_paper_is_written_for_reviewers_who_exist() -> None:
    """Seeds, intervals and reproducibility checklists are what an imitation of
    a reviewer asks for. Reading real ICLR reviews and the official guide, what
    is nearly always assessed is whether the problem is real, whether the idea
    is interesting, whether the comparison is fair, and whether the claim
    matches the evidence — statistics appear when a margin is small."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    drafting = " ".join(item.statement for item in STAGE_CHECKLISTS["review"])
    assert "one honest paragraph" in drafting
    assert "buys no protection" in drafting
    assert "not the spine of a paper" in drafting

    planner = vertical_role_banner(load_vertical("research"), "planner")
    # The moves that actually earn a strong review, named so they can be aimed at.
    for move in (
        "explaining something the field assumed it already understood",
        "connection between two areas",
        "principled method",
    ):
        assert move in planner
    assert "claim the results do not support" in planner


def test_submission_asks_whether_the_result_stands() -> None:
    """The terminal objective was "produce a paper", and submission only checked
    that one existed: three of its four items were packaging — PDF, BibTeX,
    anonymity, metadata — and none asked whether the result held. The campaigns
    learned the obvious lesson, recording as durable experience that "a visually
    polished Stage 1 diagnostic can be certified as a release-ready ICLR final
    submission". Scoping down until it certifies is the reward hack."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    submission = STAGE_CHECKLISTS["submission"]
    first = " ".join(submission[0].statement.split())

    # The result question comes before the packaging questions.
    assert submission[0].id == "submission.result_stands"
    assert "beat the baseline it was chosen against" in first
    assert "a gap to close, not a finding to package" in first
    assert "delivers a paper without delivering a result" in first


def test_an_unfinished_implementation_is_not_a_dead_idea() -> None:
    """Over-confidence and under-confidence are the same bug: a number read as a
    verdict on the idea when it was really a verdict on the engineering."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    first = " ".join(STAGE_CHECKLISTS["submission"][0].statement.split())

    assert "until the baseline reproduces in this harness" in first
    assert "an unfinished implementation looks exactly like a wrong idea" in first


def test_the_win_threshold_is_derived_not_invented() -> None:
    """A campaign declared it needed "+8 absolute points". Nothing produced that
    number; asking for "the win that would matter" invites a round figure that
    sounds decisive and that no evidence can contradict."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    planner = " ".join(vertical_role_banner(load_vertical("research"), "planner").split())

    assert "observed benchmark spread" in planner
    assert "published gaps" in planner
    assert "never a convenient round number" in planner


def test_the_model_choice_is_read_as_a_claim_about_currency() -> None:
    """Campaigns kept reaching for checkpoints that were two generations old,
    because that is what a training cutoff leaves behind."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    engineer = " ".join(vertical_role_banner(load_vertical("research"), "engineer").split())

    assert "Verify current models" in engineer
    assert "live sources instead of memory" in engineer


def test_stopping_is_separated_from_being_wrong() -> None:
    """Three rules I wrote today deadlocked: a miss said nothing about the idea,
    retirement demanded proof the next round would fail, and submission demanded
    success. The only lawful states left were win, cheat the gate, or grind
    forever. Separating the scientific, spending and publication decisions is
    what gives persistence an exit that is not a false verdict."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    gate = " ".join(STAGE_CHECKLISTS["submission"][0].statement.split())

    assert "Three decisions live here and must not be collapsed" in gate
    assert "an opportunity-cost call, not a verdict that the idea was false" in gate
    # And abstention has to be a legal ending, or the contract gets weakened instead.
    assert "no qualifying result inside the budget is an honest ending" in gate
    assert "will eventually weaken its own contract to ship one" in gate
