"""Tests for the research 'what we promised at selection' block.

The block is PURE VISIBILITY (no verdict): it re-surfaces what the campaign
itself wrote into ``research/IDEA_SELECTION.json`` before the work began, so a
role reading a result can see it next to the promise. It must never decide
whether the baseline was strong or the margin was cleared.

The file is Agent-authored, so its shape differs every campaign. These tests
pin the intent-matching (nested, differently-named), the visible record of
promises never filed, and the fail-soft contract.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.verticals._base import load_vertical, vertical_search_altitude
from argus_skill.verticals.research.stages import _selection_contract_block


def _write(root, payload: object) -> None:
    d = root / "research"
    d.mkdir(parents=True, exist_ok=True)
    (d / "IDEA_SELECTION.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )


def test_flat_contract_renders_every_promise(tmp_path):
    _write(
        tmp_path,
        {
            "central_uncertainty": "Do steering vectors transport across models?",
            "end_task_claim": "Beats the target-trained baseline on held-out control",
            "strongest_resource_matched_baseline": "Prompt steering, same calibration",
            "meaningful_win_threshold": "Above seed spread on 3 of 4 splits",
        },
    )
    block = _selection_contract_block(tmp_path)
    assert "promised at selection" in block
    for expected in (
        "question: Do steering vectors transport",
        "end task: Beats the target-trained baseline",
        "baseline to beat: Prompt steering",
        "margin that would count: Above seed spread",
    ):
        assert expected in block
    assert "never filed" not in block


def test_promises_are_found_when_nested_and_renamed(tmp_path):
    """A real campaign filed these three levels down under different names."""
    _write(
        tmp_path,
        {
            "selected": {
                "consequential_uncertainty": "Is it mechanism or correlation?",
                "strongest_resource_matched_baseline": {
                    "primary": "CircuitSteer at matched budget"
                },
            },
            "claim_contract": {
                "end_task": "Compose 3-5 simultaneous internal controls",
                "meaningful_win_size": "+10 absolute constraint satisfaction",
            },
        },
    )
    block = _selection_contract_block(tmp_path)
    assert "question: Is it mechanism or correlation?" in block
    assert "baseline to beat: primary: CircuitSteer at matched budget" in block
    assert "end task: Compose 3-5 simultaneous" in block
    assert "margin that would count: +10 absolute" in block


def test_a_promise_never_filed_is_itself_visible(tmp_path):
    """Two live campaigns named no baseline and no margin. Say so."""
    _write(tmp_path, {"selected_idea": {"claim_scope": "FRDM improves the Pareto"}})
    block = _selection_contract_block(tmp_path)
    assert "end task: FRDM improves the Pareto" in block
    assert "never filed: question, baseline to beat, margin that would count" in block


def test_the_block_states_no_verdict(tmp_path):
    """Rendering facts is the whole job; judging them belongs to the reader."""
    _write(
        tmp_path,
        {
            "central_uncertainty": "q",
            "strongest_resource_matched_baseline": "b",
            "meaningful_win_threshold": "+2 points",
        },
    )
    block = _selection_contract_block(tmp_path).lower()
    for verdict in ("too weak", "insufficient", "fails", "not met", "violation"):
        assert verdict not in block


def test_shallower_wins_when_a_name_repeats(tmp_path):
    _write(
        tmp_path,
        {
            "end_task_claim": "the real one",
            "notes": {"end_task_claim": "a stale copy"},
        },
    )
    assert "end task: the real one" in _selection_contract_block(tmp_path)


@pytest.mark.parametrize(
    "payload", ["{not json", "[]", '"a string"', json.dumps({"unrelated": 1})]
)
def test_fail_soft_never_raises(tmp_path, payload):
    _write(tmp_path, payload)
    assert _selection_contract_block(tmp_path) == ""


def test_missing_file_is_silent(tmp_path):
    assert _selection_contract_block(tmp_path) == ""


def test_promise_reaches_roles_through_the_vertical_hook(tmp_path):
    """It must ride the block every role already receives, exemplars or not."""
    _write(tmp_path, {"end_task_claim": "the claim under test"})
    block = vertical_search_altitude(load_vertical("research"), tmp_path)
    assert "the claim under test" in block


def test_a_run_that_cannot_see_the_win_does_not_retire_the_idea() -> None:
    """One campaign called a 0.73-standard-error gap decisive and quit.

    Its whole results table spanned about one standard error, with no error bar
    anywhere in the paper. Policy has to say that a run whose noise is wider
    than the promised margin has not tested anything -- without naming a
    threshold, since the margin is the one the campaign itself declared.
    """
    from argus_skill.verticals.research.stages import _AMBITIOUS_RESEARCH_POLICY

    policy = _AMBITIOUS_RESEARCH_POLICY.lower()
    assert "could have seen the win" in policy
    assert "spread of your own repeated measurements" in policy
    assert "margin declared at selection" in policy
    assert "only failed to look at it" in policy
    # The bar is the campaign's own declared margin, never a number we invent.
    for invented in ("0.05", "95%", "three seeds", "p <"):
        assert invented not in policy


def test_a_table_has_to_show_who_won() -> None:
    """A delivered paper made the reader work out which row was the method."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(i for i in STAGE_CHECKLISTS["review"] if i.id == "review.tables")
    statement = item.statement.lower()
    assert "as ours" in statement
    assert "bold the winning number" in statement
    assert "caption" in statement


def test_the_paper_quality_chain_has_no_missing_link() -> None:
    """Each fix below is worthless alone; the paper only improves if all hold.

    A campaign declares what would count, is shown that promise while it works,
    treats a miss as a repair rather than a refutation, is stopped from
    retiring an idea on a run too coarse to see it, can only be closed by the
    Manager, must say at submission whether the result stands, and finally has
    to present it so a reader sees who won. Break one link and the chain leaks
    back to shipping a null result dressed as a finding.
    """
    from argus_skill.verticals.research import stages

    policy = stages._AMBITIOUS_RESEARCH_POLICY
    checklists = " ".join(
        item.statement for group in stages.STAGE_CHECKLISTS.values() for item in group
    ).lower()

    # 1. selection names the baseline and the margin
    assert "strongest resource" in stages._PLANNER_RESEARCH_ORCHESTRATION.lower()
    # 2. that promise is put back in front of every role
    assert "promised at selection" in stages.search_altitude_context.__doc__
    # 3. a miss is a repair, not a refutation
    assert "debugging signal" in policy
    # 4. a run too coarse to see the win cannot retire the idea
    assert "could have seen the win" in policy
    # 5. only the Manager closes an idea, and it costs
    assert "rare and expensive" in stages._MANAGER_RESEARCH_STEWARDSHIP
    # 6. submission asks whether the result stands
    assert any(i.id == "submission.result_stands" for i in stages.STAGE_CHECKLISTS["submission"])
    # 7. no defensive paper that lists what it declines to claim
    assert "listing non-claims" in policy
    # 8. the work is measured against papers that were actually accepted
    assert "accepted same-area" in checklists
    # 9. and the reader can see who won
    assert "as ours" in checklists
    # 10. and no earlier acceptance can settle a number nobody outside checked
    from argus_skill.roles.prompts.reviewer import _INCREMENTAL_REREVIEW_BOUNDARY

    assert "acceptance never settles" in _INCREMENTAL_REREVIEW_BOUNDARY


def test_a_broken_harness_cannot_certify_itself() -> None:
    """A campaign measured 6% where the model's published score is ~80%.

    Every rollout had hit its token cap, so the pipeline was what got measured,
    and the paper reported the result as a boundary finding. Reproducing your
    own broken baseline proves nothing; the absolute check is the published
    number for the same model and benchmark.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        i for i in STAGE_CHECKLISTS["benchmark"] if i.id == "benchmark.evaluator_authentic"
    )
    text = (item.statement + " " + item.evidence_hint).lower()
    assert "published" in text
    assert "truncation rate" in text or "hits its own limits" in text


def test_a_title_names_a_finding_not_a_genre() -> None:
    """Two delivered papers titled themselves 'A Boundary Study' and 'on a
    Substituted ... Layer-20 Model' -- a genre label and an apology."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex")
    text = item.statement.lower()
    assert "a boundary study" in text
    assert "apology has put the excuse where the result belongs" in text
    assert "no tightly" in text and "fenced claim" in text


def test_the_evidence_run_is_sized_to_convince_not_to_save() -> None:
    """Three campaigns read 'cheapest faithful run' as 'fewest examples'.

    They claimed a win of three examples on 120 and of one on 48 -- around half
    a standard error, which no reviewer reads as a result. Naming the evidence
    run after its cost was the invitation; the run is now named after the reader
    it has to convince, and cheapness is left to the feasibility probes.
    """
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    planner = _PLANNER_RESEARCH_ORCHESTRATION.lower()
    assert "cheapest faithful run" not in planner
    assert "wants the claim to be false" in planner
    assert "scale is part of the argument, not a cost to minimize" in planner
    # The sizing bar stays the campaign's own observed spread, not a fixed n.
    assert "outside their own spread" in planner
    # A budget that cannot buy the convincing run is staged, never shrunk.
    assert "stage it and buy it in pieces" in planner


def test_a_long_wait_is_not_an_idle_campaign() -> None:
    """Every campaign ran one mission while configured for two.

    Rounds 1-3 of one campaign spent about eighteen hours waiting on GPU work
    with nothing else queued and no pending mission behind it. Waiting does not
    consume the round budget, so the cost was pure wall-clock.
    """
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    planner = _PLANNER_RESEARCH_ORCHESTRATION.lower()
    assert "will sit for hours on external" in planner
    assert "does not need its result" in planner
    assert "wall-clock is most of what a paper costs" in planner


def test_a_suppressed_status_probe_points_somewhere() -> None:
    """The planner learned that waiting was the only move.

    While durable work runs the Host drops status-probe tasks, which is right,
    but the reason it returned described only the refusal. Cycle after cycle
    the planner scheduled nothing else and campaigns ran one mission of two
    through eighteen-hour waits.
    """
    from pathlib import Path

    from argus_skill.life.supervisor import _planning_cycle

    source = Path(_planning_cycle.__file__).read_text(encoding="utf-8")
    assert "Waiting is not" in source
    assert "does not need this job's result" in source
    assert "Only status probes are suppressed here." in source


def test_the_planner_is_told_it_has_more_than_one_slot() -> None:
    from argus_skill.roles.prompts import planner as planner_prompts
    from pathlib import Path

    text = Path(planner_prompts.__file__).read_text(encoding="utf-8")
    assert "More than one mission runs at a time" in text
    assert "leaves the rest of the campaign idle" in text


def test_a_wait_grants_the_planner_one_turn_not_none_and_not_every_cycle() -> None:
    """The hard half of the speed bottleneck.

    A wait contract skipped the Planner outright until the watched revision
    moved, so on a multi-hour GPU job it was not asked anything for hours and
    the campaign's other mission slots stayed empty. It also must not be woken
    every cycle, which is the token-burning poll the skip exists to prevent.
    """
    from pathlib import Path

    from argus_skill.life.supervisor import _planning_context

    source = Path(_planning_context.__file__).read_text(encoding="utf-8")
    assert "idle_capacity_turn_used" in source
    assert "One turn," in source and "not one per cycle" in source
    # The grant is conditional on the campaign actually being idle.
    assert "_nothing_queued_behind_the_wait" in source
    # And it survives the suppression path rebuilding the contract each cycle.
    assert "belongs to the blocker, not to" in source
def test_a_review_that_cannot_fail_is_not_a_review() -> None:
    """Three campaigns ran 321 reviews and never once returned `incorrect`.

    The Reviewer was asked only relative questions -- not all zeros, not
    trivially weak -- and 6% on a benchmark the model publishes ~80% on passes
    both of them. Saying `verified` was free because nothing outside the
    harness was ever consulted, so the review cost tokens and changed nothing.
    """
    from pathlib import Path

    import argus_skill

    skill = (
        Path(argus_skill.__file__).parent
        / "verticals/research/skills/reviewer/experiment-results-review.md"
    ).read_text(encoding="utf-8").lower()

    # the outside anchor the reviewer must fetch before trusting anything above it
    assert "what does the literature report for *this* model on *this* benchmark" in skill
    assert "the harness is what you measured" in skill
    assert "hit their own token or step limit" in skill
    # and falling short of it ends the review instead of scoring it
    blockers = skill.split("## hard blockers")[1]
    assert "far under the published score" in blockers
    assert "narrower than the spread of the run's own repeats" in blockers


def test_a_qualifier_in_the_title_has_to_be_earned() -> None:
    """run-06 titled itself 'A Frozen Environment-Invariant Causal Subspace Does
    Not Beat Prompt Steering on AxBench' after a decisive 750-row loss -- and it
    only ever ran the frozen variant. A reader cannot tell from that whether
    causal steering failed or freezing did, so the negative result does not
    answer the question the abstract poses.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    draft = next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex").statement
    assert "A qualifier you chose is itself a claim" in draft
    assert "narrow the claim to what you did test" in draft
    # And a negative result is only publishable once it survives the engineering.
    assert "name in the abstract the belief it kills" in " ".join(draft.split())


def test_a_table_is_written_for_a_person() -> None:
    """The same paper printed 0.6946666666666667 and -0.35733333333333334."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    tables = next(i for i in STAGE_CHECKLISTS["review"] if i.id == "review.tables").statement
    assert "as ours" in tables and "bold the winning number" in tables
    assert "Round every number to the precision its evidence supports" in tables


def test_the_venue_every_campaign_targets_is_in_the_registry() -> None:
    """Seven campaigns were told to write ICLR papers against a registry that
    held only EMNLP, AAAI and a Frontiers journal. One stopped and asked the
    operator how to proceed; the wrong answer was available and silent, because
    an EMNLP profile would have imposed a two-column eight-page layout on an
    ICLR submission without anything reporting a mismatch.
    """
    from argus_skill.verticals.research.venue_profiles import get_venue_profile

    for token in ("ICLR", "iclr2027", "ICLR 2027", "iclr-27"):
        profile = get_venue_profile(token)
        assert profile.key == "ICLR"
        # 9 pages of main text; references and appendix are uncounted.
        assert profile.body_page_limit == 9
        assert profile.references_min_page == 10
        # ICLR is the one single-column venue here.
        assert profile.two_column is False
        # Anonymity is the default state, so there is no review option to pass.
        assert profile.review_option == ""
        assert "iclr2027_conference" in profile.review_mode_macro

    # NeurIPS and ICML have their own limits and templates; resolving them onto
    # ICLR would be the same silent mismatch in a new direction.
    for other in ("NEURIPS", "ICML"):
        with pytest.raises(KeyError):
            get_venue_profile(other)


def test_a_number_printed_at_float_precision_is_reported(tmp_path) -> None:
    """One draft carried thirteen of them: 521/750 appeared as
    0.6946666666666667 and the paired delta as -0.35733333333333334. Prose in a
    checklist did not stop it, and no measurement carries seventeen significant
    digits -- a decimal that long was printed, not reported.
    """
    from argus_skill.verticals.research.paper_structural_minimums import (
        validate_paper_structural_minimums,
    )

    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"\documentclass{article}\begin{document}"
        r"Ours reaches 0.6946666666666667 against 0.337 (delta 0.42, n=750)."
        r"\end{document}",
        encoding="utf-8",
    )

    codes = {
        issue.code: issue.detail
        for issue in validate_paper_structural_minimums(tmp_path).issues
    }
    assert "unrounded_float_repr" in codes
    detail = codes["unrounded_float_repr"]
    # The count and one example are rendered; the harness does not rewrite it.
    assert "1 number(s)" in detail
    assert "0.6946666666666667" in detail
    # Numbers a person would actually write are left alone.
    assert "0.337" not in detail and "0.42" not in detail


def test_a_baseline_named_after_a_paper_has_to_be_that_paper() -> None:
    """Six of seven campaigns filed a comparison that was not the comparison it
    claimed. One lexical routing score -- 0.54*question_overlap +
    0.38*choice_overlap + 0.08*recency -- appeared three times under the names
    H2O, SnapKV and PyramidKV, all three of which are attention-based. A lower
    learning rate appeared as SAR, whose contribution is sharpness-aware
    minimisation. No checker can verify that select_h2o_proxy implements H2O,
    which is exactly why the rule has to be stated rather than measured.
    """
    from pathlib import Path

    import argus_skill
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    plan = next(
        i for i in STAGE_CHECKLISTS["plan"] if i.id == "plan.experiment"
    ).statement
    assert "must be that method" in plan
    assert "drop the published name" in plan
    # Counting rows is not counting comparisons.
    assert "count families" in plan

    review = (
        Path(argus_skill.__file__).parent
        / "verticals/research/skills/reviewer/experiment-results-review.md"
    ).read_text(encoding="utf-8")
    assert "was that method's own" in review
    blockers = review.split("## Hard blockers")[1]
    assert "carrying a published method's name that is not that method" in blockers


def test_an_evaluation_has_to_detect_the_case_it_cannot_miss() -> None:
    """Three truncations reached three papers: 6% on MATH-500 with every rollout
    at its cap, concept hits scored on 40-token generations, and a 16-cell sweep
    concluding that five steering methods all sit at chance -- measured on twelve
    tokens. That last one carried its own refutation in the same table: concept
    prompting, where the model is told outright to mention the concept, also sat
    at chance. A positive control that cannot be detected condemns the
    instrument, and it costs one run.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        i for i in STAGE_CHECKLISTS["benchmark"] if i.id == "benchmark.evaluator_authentic"
    )
    text = item.statement
    assert "positive control" in text
    assert "cannot be separated from random" in text
    assert "nothing from that harness means anything" in text


def test_queueing_work_beside_a_run_says_how_to_make_it_claimable() -> None:
    """The planner has been told to queue work beside a long run for a while and
    six of seven campaigns still ran one mission at a time. The rule is
    unforgiving and was never stated: Backlog._parallel_worker_can_claim refuses
    every candidate while ANY running item lacks parallel_safe or owns_paths, so
    one unmarked GPU run switches parallelism off campaign-wide.
    """
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    planner = _PLANNER_RESEARCH_ORCHESTRATION
    assert "`parallel_safe` with a concrete `owns_paths` list" in planner
    assert "every running task" in planner
    assert "switches parallelism off for the whole" in planner


def test_the_accepted_papers_block_says_to_count_what_they_carry(tmp_path) -> None:
    """All seven papers sat far under their venue on the two counts a reviewer
    forms an impression from before checking any number: nine to eighteen
    references, and none to six figures. The accepted papers were already on
    disk and nobody counted them. The bar is the venue's own, not a threshold
    invented here, which is why it is asked as a comparison.
    """
    import json

    from argus_skill.verticals.research.argument_organization import (
        ARGUMENT_ORGANIZATION_PATH,
    )
    from argus_skill.verticals.research.stages import _accepted_papers_block

    target = tmp_path / ARGUMENT_ORGANIZATION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"exemplars": [{"title": "An Accepted Paper", "venue": "ICLR"}]}),
        encoding="utf-8",
    )

    block = _accepted_papers_block(tmp_path)
    assert "An Accepted Paper (ICLR)" in block
    assert "how many references, how many figures" in block
    assert "before deciding your own draft is done" in block


def test_a_conceded_limitation_is_the_next_experiment() -> None:
    """run-05 wrote its own rejection into its Limitations section: accepted
    papers in its area evaluate multiple model families and eleven to eighteen
    benchmarks, and it evaluated one family and six behavior families. It had
    located the highest-value remaining run in the campaign, wrote it down as a
    concession, and spent the rest of the budget polishing prose. Nothing in the
    framework turned a self-diagnosed rejection reason into work.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        i for i in STAGE_CHECKLISTS["review"] if i.id == "review.publication_value"
    ).statement
    assert "not a paragraph, it is the next experiment" in item
    assert "has chosen to concede it" in item
    # Abandoning it is allowed only as a scientific claim, not a budget excuse.
    assert "fundamental rather than merely unaffordable" in item


def test_a_null_needs_a_stronger_instrument_than_what_it_overturns() -> None:
    """run-04's contribution is that teacher-forced steering metrics do not
    predict decoded behavior. Its own Limitations concede that the detector
    establishing that null is lexical -- weaker than the metrics it unseats.
    A detector that cannot resolve a concept produces chance for every method,
    which is precisely the reported result, so the null and the broken
    instrument are indistinguishable. run-06 made the same trade in the other
    direction, using keyword matching to conclude that prompting beats internal
    steering.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        i for i in STAGE_CHECKLISTS["benchmark"]
        if i.id == "benchmark.evaluator_authentic"
    ).statement
    assert "must be stronger than the one that produced them" in item
    assert "measure with the established one and then with the better one" in " ".join(
        item.split()
    )


def test_a_caveat_is_stated_once_not_in_every_section() -> None:
    """run-01 held its ImageNet-C claim until a properly powered run landed,
    which was right, and then wrote 'pending adequately powered validation'
    fourteen times -- abstract, introduction, results, a table cell, a
    subsection heading, discussion. The science was rigorous and the manuscript
    read as unfinished, because it was organised around the absence rather than
    around what its CIFAR-10-C evidence did establish.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    draft = next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex").statement
    assert "Say each caveat once, where it belongs" in draft
    assert "organised around an absence" in " ".join(draft.split())
    assert "let every other section say what the work does establish" in draft


def test_renaming_a_baseline_does_not_replace_running_a_real_one() -> None:
    """run-02 was told to name the method or run the method and chose to rename,
    correctly: its table now says lexical-route proxy and states it is
    deliberately not named after any published method. That fixed the
    misattribution and left the campaign with zero published comparators, a
    headline of 0.608 against a 0.583 heuristic it wrote itself, and ChunkKV,
    R-KV and LOCKS cited in Related Work but absent from the table.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    plan = next(i for i in STAGE_CHECKLISTS["plan"] if i.id == "plan.experiment").statement
    assert "resolves the misattribution and not the weakness" in " ".join(plan.split())
    assert "has not been compared to the field" in plan
    assert "One real comparator at your own budget" in " ".join(plan.split())


def test_dropping_a_qualifier_can_be_worse_than_keeping_it() -> None:
    """The first version of the qualifier rule offered two exits and run-06 took
    the cheap one: it deleted 'Frozen' from its title without ever running an
    unfrozen variant, so a result about one member of a class became a claim
    about the class. That is a worse failure than the apology it replaced, and
    the rule as written permitted it.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    draft = " ".join(
        next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex").statement.split()
    )
    assert "narrow the claim to what you did test" in draft
    assert "honest only when the evidence reaches the wider class" in draft
    assert "worse than the apology it replaced" in draft


def test_a_negative_result_is_an_optimization_signal_first() -> None:
    """The rule this replaces was harmful. It told a campaign to give a negative
    result the argument its measurement earned, which legitimised writing up
    restricted negatives -- exactly the reward hack the whole policy exists to
    stop. A low number is almost always a fact about the run: 6.0% on MATH-500
    for a model published at 79.7 became 68.8% by raising a token cap and 76.4%
    by executing the tool the protocol assumes. Two settings, an order of
    magnitude, and at every stage the number read as a finding.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    draft = " ".join(
        next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex").statement.split()
    )
    assert "a fact about the world or about your run" in draft
    assert "Almost always it is the run" in draft
    assert "first an optimization signal" in draft
    # Writing it up is what happens after the engineering is good, not instead.
    assert "only after the engineering is actually good" in draft
    assert "restricted until it was cheap enough to certify" in draft


def test_a_protocol_is_worth_the_evidence_it_governs() -> None:
    """run-07 spent more than a day producing a fully specified experiment and
    no measurement: an 808-word protocol section against 704 words of
    introduction, method and results combined, and a results section stating
    outright that it reports no additive value and no IBSA value. Not a
    fabricated number -- an artifact that reads as rigour while nothing was
    measured, which is the failure mode a deadline actively rewards.
    """
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    planner = " ".join(_PLANNER_RESEARCH_ORCHESTRATION.split())
    assert "worth only the evidence it ends up governing" in planner
    assert "Specification is unbounded and costs nothing" in planner
    assert "longer than the science it protects" in planner


def test_the_paper_may_be_the_by_product() -> None:
    """run-03 filed its most valuable result as its own repair log. On one model
    and 500 rows it measured 0.060 under a truncated CoT harness, 0.688 with the
    cap raised and the scorer fixed, 0.083 under the model card's tool-integrated
    protocol with the tool not executed, and 0.764 with it executed, against
    0.797 published. That explains how a number people report against today
    reproduces anywhere from 6 to 76 percent, and the campaign called it
    blocking because BCPO was the selected idea.

    The guard against this becoming a licence to wander is that the by-product
    must already be measured; drift abandons a question for an unmeasured one.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = " ".join(
        next(
            i for i in STAGE_CHECKLISTS["review"] if i.id == "review.publication_value"
        ).statement.split()
    )
    assert "not always the selected idea" in item
    assert "already measured, replicated" in item
    assert "drift abandons a question for an unmeasured one" in item
    assert "binds what you may claim, not what you are allowed to notice" in item


def test_a_failed_positive_control_stops_the_run() -> None:
    """Adding the positive control was not enough. run-04 launched a sweep to
    rescue a twelve-token null, at twelve tokens, and its own completed cells
    reported target_concept_hit_rate 0.0, positive_target_hit_rate 0.0 and every
    other hit rate at 0.0 across a thousand prompts, with 37% of generations
    echoing the prompt back -- and it kept running. Measuring the control and
    filing it is not the point; reading it in time to stop is.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = " ".join(
        next(
            i for i in STAGE_CHECKLISTS["benchmark"]
            if i.id == "benchmark.evaluator_authentic"
        ).statement.split()
    )
    assert "Read it before the run finishes, not after" in item
    assert "stop the run rather than completing it" in item
    assert "the compute is the paper's remaining budget" in item


def test_a_slow_run_is_diagnosed_before_the_budget_is_cut() -> None:
    """run-04's generation benchmarks ran on CPU: 2472% CPU and four days of CPU
    time in four and a half wall-clock hours, while the GPU holding the model
    sat at 0% utilisation. It never diagnosed that, and instead shrank the
    generation budget to twelve tokens to make the sweeps finish -- which is how
    a performance bug became a scientific one, because at twelve tokens every
    hit rate including the positive control reads 0.0.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = " ".join(
        next(i for i in STAGE_CHECKLISTS["run"] if i.id == "run.manifests").statement.split()
    )
    assert "check that it runs on the hardware you think it does" in item
    assert "compare GPU utilisation against CPU time" in item
    assert "is not a reason to measure less" in item


def test_reviewer_suspects_the_setup_before_the_idea() -> None:
    """A wrong setting and a wrong idea produce the same artifact: a low number
    with clean plumbing. run-03 filed 6.0% on MATH-500 for a model published at
    79.7 as a boundary result; a token cap and an unexecuted tool were the whole
    story. The Reviewer sees the number first and has to treat that distance as
    a defect report rather than score it.
    """
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    reviewer = " ".join(vertical_role_banner(load_vertical("research"), "reviewer").split())
    assert "is a defect report, not a finding" in reviewer
    assert "send it back to be rebuilt" in reviewer
    # Named because they are the ones that actually happened, then generalised.
    assert "generation cap shorter than the answer needs" in reviewer
    assert "executing the tool in tool-integrated reasoning" in reviewer
    assert "shorter than what the task requires at inference" in reviewer
    assert "examples, not a list to tick" in reviewer
    assert "which single setting, if wrong, would produce exactly the number" in reviewer


def test_manager_keeps_the_campaign_optimizing_instead_of_settling() -> None:
    """Every campaign here drifted toward writing up whatever it had. The
    Manager holds the altitude where that is visible: keep buying fixes while
    budget remains, and separately notice a run that is not worth improving
    because it was broken."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    manager = " ".join(vertical_role_banner(load_vertical("research"), "manager").split())
    assert "many rounds of better engineering and larger runs" in manager
    assert "the campaign that stops early is the one that ships a bounded negative" in manager
    assert "not worth improving because it was broken" in manager
    assert "instead of letting the campaign interpret it" in manager


def test_the_setup_skill_teaches_the_shape_not_a_checklist() -> None:
    """Listing the settings that burned us is not enough -- the next campaign
    meets a different one. The skill has to name the general form so a campaign
    can enumerate the equivalents in its own pipeline."""
    from pathlib import Path

    import argus_skill

    skill = (
        Path(argus_skill.__file__).parent
        / "verticals/research/skills/engineer/suspect-the-setup.md"
    ).read_text(encoding="utf-8")

    for seen in ("6.0%", "68.8%", "76.4%"):
        assert seen in skill
    assert "Training sequence length" in skill
    assert "teaches the model to stop early" in skill
    assert "Reasoning about settings not listed here" in skill
    assert "bounds what the model is allowed to produce" in skill
    # And it must not end at the repair.
    assert "Stopping at the first honest measurement" in skill


def test_the_field_harness_is_adopted_before_one_is_written() -> None:
    """Root cause of every weak paper here, measured across 422 missions in
    seven campaigns: 68% repaired self-built measurement code and 6% improved
    the method. Each campaign wrote thousands of lines of its own evaluation
    stack, and every defect that manufactured a negative result lived in it --
    twelve-token caps, unexecuted tool steps, keyword scorers, CPU-bound
    generation, train-mode BatchNorm in a baseline. From inside, none of those
    look like bugs; they look like findings.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = " ".join(
        next(i for i in STAGE_CHECKLISTS["plan"] if i.id == "plan.benchmark").statement.split()
    )
    assert "Take the field's own harness before writing any of your own" in item
    assert "released implementation of each baseline" in item
    assert "they look like negative results" in item
    assert "which existing harness and which baseline implementations you are adopting" in item
    # And the point of saving those hours is stated, not left implied.
    assert "spend the hours that frees on method iteration and scale" in item


def test_manager_watches_the_mission_ratio() -> None:
    """A method almost never wins in its first form. If the campaign has run
    many rounds and none of them proposed a stronger version of the idea, it is
    maintaining infrastructure rather than doing research -- which is exactly
    what 68% versus 6% describes."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    manager = " ".join(vertical_role_banner(load_vertical("research"), "manager").split())
    assert "that ratio is the campaign's real strategy" in manager
    assert "A method almost never wins in its first form" in manager
    assert "maintaining infrastructure rather than doing research" in manager


def test_the_experiment_is_designed_not_only_diagnosed() -> None:
    """Operator: it cannot design an experiment. It set output tokens to 12 for a
    maths evaluation and reported 6% accuracy, and it configures common
    post-training badly. Diagnosing a bad number afterwards is not enough -- the
    settings that ruin a result are chosen once, early, and never revisited.
    """
    from pathlib import Path

    import argus_skill

    skill = (
        Path(argus_skill.__file__).parent
        / "verticals/research/skills/engineer/suspect-the-setup.md"
    ).read_text(encoding="utf-8")

    assert "## Design it before you run it" in skill
    assert "a cap of twelve is not an experiment" in " ".join(skill.split())
    # Post-training is where a bad setting is least visible.
    assert "RL post-training (GRPO, PPO, RLVR and relatives)" in skill
    assert "teaches the model to stop early" in skill
    assert "template mismatch destroys a result" in " ".join(skill.split())
    assert "read the raw outputs with your own eyes" in skill


def test_recalling_a_model_is_evidence_that_it_is_old() -> None:
    """Every campaign reached for the same stale model families. The cause is
    not laziness: a model you can name from memory is one your training data
    covered heavily, which means it was widespread long before now."""
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = " ".join(
        next(i for i in STAGE_CHECKLISTS["plan"] if i.id == "plan.backbone").statement.split()
    )
    assert "evidence that it is old, not that it is suitable" in item
    assert "where the live catalog disagrees with what you remember, the catalog is right" in item


def test_rigour_apparatus_is_proportional_to_the_doubt() -> None:
    """Campaigns hashed every artifact and planned multi-seed repeats while
    their evaluation could not detect its own positive control. Seeds, repeats
    and hashes cost the hours the method needed, and answer nothing when the gap
    is enormous or the instrument is broken."""
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    manager = " ".join(vertical_role_banner(load_vertical("research"), "manager").split())
    assert "Rigour apparatus is proportional too, and it is not free" in manager
    assert "where the answer is genuinely in doubt" in manager
    assert "more seeds answer nothing" in manager


def test_the_campaign_is_trying_to_win() -> None:
    """Seven campaigns measured carefully, reported honestly and produced
    nothing citable, because none was trying to succeed -- only trying not to
    overclaim. Burying a real win is a misreport too, and the commoner one."""
    from argus_skill.verticals.research.stages import _AMBITIOUS_RESEARCH_POLICY as policy

    text = " ".join(policy.split())
    assert "You are trying to win, not trying to be safe" in text
    assert "only trying not to overclaim" in text
    assert "say so plainly and immediately, in the first sentence, with the number" in text
    assert "burying a real win under qualifications is as much a misreport as inventing one" in text


def test_training_below_its_own_baseline_is_a_defect_report() -> None:
    """run-03 finished a clean 500-row official-protocol comparison: BCPO 0.738,
    CSCR-style 0.742, no-pairing 0.736, GRPO-broadcast 0.738 -- all four below
    the untrained base at 0.756. Its recorded decision was that BCPO does not
    beat the strongest resource-matched baseline, which ranks the variants and
    buries the fact that training degraded the model. The cause was in its own
    metrics: 97.2% of training completions clipped, mean training output 191
    tokens for a task whose solutions average 637.
    """
    from pathlib import Path

    import argus_skill
    from argus_skill.verticals._base import load_vertical, vertical_role_banner

    reviewer = " ".join(vertical_role_banner(load_vertical("research"), "reviewer").split())
    assert "below its own untrained starting checkpoint" in reviewer
    assert "ranking the variants against each other hides it" in reviewer
    assert "untrained checkpoint under the identical protocol in every table" in reviewer

    skill = (
        Path(argus_skill.__file__).parent
        / "verticals/research/skills/engineer/suspect-the-setup.md"
    ).read_text(encoding="utf-8")
    assert "every\ntrained variant scoring below the untrained starting checkpoint" in skill
    assert "97% of its training completions clipped" in " ".join(skill.split())


def test_being_out_of_experiments_is_not_being_finished() -> None:
    """run-04's planner declared the project done with a 3,114-word manuscript,
    twelve citations and four figures -- on a mechanism result whose held-out
    survival predictor reaches AUROC 0.970. The completion decision asked
    whether the claim was supported and whether to keep spending, and never
    asked whether the paper was written. The comparison it needed was already
    on its own disk.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    gate = " ".join(
        next(
            i for i in STAGE_CHECKLISTS["submission"] if i.id == "submission.result_stands"
        ).statement.split()
    )
    assert "whether the manuscript is finished" in gate
    assert "accepted same-area papers already on disk and compare what they carry" in gate
    assert "Being out of experiments is not the same as being finished" in gate
    assert "what remains at that point needs no compute at all" in gate


def test_a_long_mission_keeps_getting_planning_opportunities(tmp_path, monkeypatch) -> None:
    """Two campaigns had their manuscripts untouched for twelve hours while a
    GPU mission ran, with an idle mission slot the whole time.

    Two defects. The Planner was asked once per set of running missions, so a
    six-hour job got one opportunity at the moment it started. And planning was
    skipped whenever anything was pending -- but a pending item the parallel
    worker cannot claim leaves the slot idle forever while looking like queued
    work, so that guard wedged the campaign permanently.
    """
    from types import SimpleNamespace

    from argus_skill.daemon.state import write_continuous_config
    from argus_skill.life.memory import Backlog, BacklogItem
    from argus_skill.life.supervisor import _core
    from argus_skill.life.supervisor._core import LifeSupervisor

    life = tmp_path / "state" / "projects" / "s-1"
    life.mkdir(parents=True)
    write_continuous_config(
        life, enabled=True, objective="beat the published baseline", open_ended=True
    )
    backlog = Backlog(life / "backlog.jsonl")
    running = backlog.add(BacklogItem.new(title="long GPU run", objective="hours"))
    backlog.update(running.id, status="running")
    running = next(r for r in backlog.all() if r.id == running.id)

    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.config = SimpleNamespace(
        continuous=False, continuous_objective="", poll_interval_seconds=30.0
    )
    sup.memory = SimpleNamespace(
        root=tmp_path / "state", project_root=life, backlog=backlog
    )
    sup._parallel_plan_fingerprint = None
    sup._parallel_plan_after = 0.0

    clock = [100.0]
    monkeypatch.setattr(_core.time, "monotonic", lambda: clock[0])
    planned: list[str] = []

    def _plan() -> None:
        planned.append(sup.config.continuous_objective)
        if len(planned) == 1:
            # Queued, but the running item is not parallel_safe, so the worker
            # cannot claim it. This is the state that used to wedge planning.
            item = BacklogItem.new(title="write the paper", objective="expand it")
            backlog.add(item)
            backlog.update(item.id, parallel_safe=True, owns_paths=["paper"])

    sup._plan_next_work = _plan

    sup._plan_alongside_running_work([running])
    assert planned == ["beat the published baseline"]
    assert backlog.next_pending(parallel_only=True) is None  # unclaimable

    # Immediately after, no spin.
    sup._plan_alongside_running_work([running])
    assert len(planned) == 1

    # After the interval the loop already uses, it asks again rather than
    # staying idle for the rest of the mission.
    clock[0] += _core._IDLE_BACKOFF_CAP_SECONDS + 1
    sup._plan_alongside_running_work([running])
    assert len(planned) == 2

    # A stopped campaign is never planned for.
    write_continuous_config(life, enabled=False, objective="", done_reason="stopped")
    clock[0] += _core._IDLE_BACKOFF_CAP_SECONDS + 1
    sup._plan_alongside_running_work([running])
    assert len(planned) == 2

    # And once real claimable work exists, planning stops: the slot is spoken for.
    write_continuous_config(
        life, enabled=True, objective="beat the published baseline", open_ended=True
    )
    backlog.update(running.id, parallel_safe=True, owns_paths=["results"])
    clock[0] += _core._IDLE_BACKOFF_CAP_SECONDS + 1
    sup._plan_alongside_running_work([running])
    assert len(planned) == 2


def test_the_planner_cannot_return_its_own_schema_example() -> None:
    """The planner prompt's JSON example is a realistic-looking task, and the
    Planner returns it verbatim often enough to matter. run-01 spent a mission
    slot on "Does pruning beat 4-bit at equal latency?" -- objective "match
    latency, read top-1", 25 characters, node key k1 -- in place of the
    claim-bearing ImageNet-C experiment it had just prepared, and run-02
    produced the identical row a day later. From outside it is indistinguishable
    from a real task; I nearly aborted run-01's real experiment because of it.
    """
    import pytest

    from argus_skill.planner.bounded_dag import _validate
    from argus_skill.roles.prompts.planner import _PLANNER_DECISION_PAYLOAD_EXAMPLE

    example_title = "Does pruning beat 4-bit at equal latency?"
    example_objective = "match latency, read top-1"
    # The guard is only correct while it matches the example actually shipped.
    assert example_title in _PLANNER_DECISION_PAYLOAD_EXAMPLE
    assert example_objective in _PLANNER_DECISION_PAYLOAD_EXAMPLE

    leaked = {
        "reason": "why",
        "tasks": [{
            "key": "k1", "deps": [],
            "title": example_title, "objective": example_objective,
            "scope": "bounded",
        }],
    }
    with pytest.raises(ValueError, match="schema example"):
        _validate(leaked)

    real = {
        "reason": "the gate now fires and the comparison can be trusted",
        "tasks": [{
            "key": "k1", "deps": [],
            "title": "Does active-gate DARC beat official EATA at matched scale?",
            "objective": "Run 915 examples per slice against official EATA and SAR.",
            "scope": "bounded",
        }],
    }
    assert len(_validate(real)[1]) == 1


def test_figures_drawn_and_left_on_disk_are_reported(tmp_path) -> None:
    """Drawing the figure is the expensive half and campaigns keep leaving it
    behind. One paper used four images with thirty-one beside it; two rewrote
    their manuscripts and dropped every include, landing at zero figures with
    the files still in paper/figures -- including the Fisher-Gram spectrum that
    was the whole thesis of that paper. The figure count alone never said so.
    """
    from argus_skill.verticals.research.paper_structural_minimums import (
        validate_paper_structural_minimums,
    )

    paper = tmp_path / "paper"
    figures = paper / "figures"
    figures.mkdir(parents=True)
    (paper / "main.tex").write_text(
        r"\documentclass{article}\begin{document}"
        r"\includegraphics[width=\linewidth]{figures/used.pdf}"
        r"\end{document}",
        encoding="utf-8",
    )
    for name in ("used.pdf", "unused.pdf", "unused.svg", "rendered_main_page-1.png"):
        (figures / name).write_bytes(b"%PDF-1.4\n")

    report = validate_paper_structural_minimums(tmp_path)
    detail = {n.code: n.detail for n in report.notes}["figures_drawn_but_unused"]
    # A spare image is a fact about the paper, not a reason it is not one.
    assert "figures_drawn_but_unused" not in {i.code for i in report.issues}
    # One drawing, not two files: the .svg is the same figure exported twice.
    assert "1 figure file(s)" in detail
    assert "unused.pdf" in detail
    # An included figure and a build artifact are not unused work. Checked on
    # the count, because "used.pdf" is a substring of "unused.pdf".
    assert "2 figure" not in detail and "3 figure" not in detail
    assert "rendered_main" not in detail


def test_backlog_never_claims_the_planner_prompts_example(tmp_path) -> None:
    """The planner's schema example was returned verbatim as a real plan and
    stored. Rejecting it at planning time came too late for the copies already
    in the backlog: run-04 claimed one and spent a mission on "Does pruning
    beat 4-bit at equal latency?" three minutes after the fix was deployed,
    because a stored item is claimed without being planned again.
    """
    import time

    from argus_skill.life.memory import Backlog, BacklogItem

    title = "Does pruning beat 4-bit at equal latency?"
    backlog = Backlog(tmp_path / "backlog.jsonl")
    backlog.add(
        BacklogItem(id="example", ts=time.time(), title=title,
                    objective="match latency, read top-1")
    )
    real = backlog.add(
        BacklogItem(id="real", ts=time.time() + 1, title=title,
                    objective="Sweep sparsity on the campaign's own checkpoint.")
    )

    claimed = backlog.claim_next()
    # The example is skipped; a genuine question that happens to share the
    # title is still the campaign's work and still runs.
    assert claimed is not None
    assert claimed.id == real.id

    stored = {item.id: item for item in backlog.all()}
    assert stored[real.id].status == "running"
    example = next(i for i in stored.values() if i.id != real.id)
    assert example.status == "skipped"
    assert "example" in example.last_error


def test_a_reference_with_nobody_on_it_is_reported(tmp_path) -> None:
    """run-05 wrote all ten of its references as bare titles: correct arXiv
    numbers, real titles, no author on any of them. The reference count read as
    a thin but ordinary bibliography, so nothing ever said the paper cites work
    without saying whose it is.
    """
    from argus_skill.verticals.research.paper_structural_minimums import (
        validate_paper_structural_minimums,
    )

    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\cite{named}\cite{bare}\end{document}",
        encoding="utf-8",
    )
    (paper / "refs.bib").write_text(
        "@article{named,\n  author = {Hinton, Geoffrey E.},\n"
        "  title = {A fast learning algorithm},\n  year = {2006}\n}\n\n"
        "@misc{bare,\n  title = {Forecasting Side Effects of Activation Steering},\n"
        "  howpublished = {arXiv:2608.11227},\n  year = {2026}\n}\n",
        encoding="utf-8",
    )

    report = validate_paper_structural_minimums(tmp_path)
    detail = {n.code: n.detail for n in report.notes}["references_without_authors"]
    assert "references_without_authors" not in {i.code for i in report.issues}
    assert "1 of 2" in detail
    assert "bare" in detail


def test_verified_reading_left_out_of_the_bibliography_is_reported(tmp_path) -> None:
    """run-05 searched harder than either campaign that ended with a real
    bibliography -- sixty-one searches against forty-one -- and wrote thirteen
    papers into the ledger with full author lists. Six were never cited, and
    the ones that made it were stripped to bare titles. The reference count
    read as an ordinary thin bibliography, so nothing said the reading had
    already been done and lost on the way to the page.
    """
    import json

    from argus_skill.verticals.research.stages import _literature_ledger_block

    (tmp_path / "research").mkdir()
    (tmp_path / "paper").mkdir()
    (tmp_path / "research" / "LITERATURE_GROUNDING.json").write_text(
        json.dumps({"papers": [
            {"title": "SAEBench: A Comprehensive Benchmark for Sparse Autoencoders",
             "authors": ["Karvonen, Adam"]},
            {"title": "Forecasting Side Effects of Activation Steering",
             "authors": ["Ong, Chong Yong"], "arxiv_id": "2608.11227"},
        ]}),
        encoding="utf-8",
    )
    # Punctuation differs from the ledger title on purpose: a colon must not
    # hide a citation that is really there.
    (tmp_path / "paper" / "references.bib").write_text(
        "@inproceedings{saebench,\n  author = {Karvonen, Adam},\n"
        "  title = {SAEBench -- A Comprehensive Benchmark for Sparse Autoencoders},\n"
        "  year = {2025}\n}\n",
        encoding="utf-8",
    )

    block = _literature_ledger_block(tmp_path)
    assert "2 verified papers" in block
    assert "one of them is not cited" in block
    assert "Forecasting Side Effects" in block

    # Nothing to say once the reading reaches the page.
    (tmp_path / "paper" / "references.bib").write_text(
        "@misc{f,\n  author = {Ong, C},\n  title = {t},\n"
        "  eprint = {2608.11227}\n}\n"
        "@inproceedings{saebench,\n  author = {Karvonen, Adam},\n"
        "  title = {SAEBench: A Comprehensive Benchmark for Sparse Autoencoders},\n"
        "  year = {2025}\n}\n",
        encoding="utf-8",
    )
    assert _literature_ledger_block(tmp_path) == ""


def test_draft_length_is_shown_against_the_campaigns_own_exemplars(tmp_path) -> None:
    """Every campaign named same-area accepted papers and pulled their full
    text to disk, and nothing ever compared the two. run-01's manuscript is a
    fifth the length of the two ICLR papers it chose and reads from the inside
    like a finished short paper. A fixed word quota was deliberately never
    added because it teaches a draft to pad; the campaign's own exemplars are a
    standard it already agreed to.
    """
    import json

    from argus_skill.verticals.research.argument_organization import (
        ARGUMENT_ORGANIZATION_PATH,
    )
    from argus_skill.verticals.research.stages import _manuscript_scale_block

    org = tmp_path / ARGUMENT_ORGANIZATION_PATH
    org.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "paper").mkdir(exist_ok=True)
    (tmp_path / "paper" / "main.tex").write_text(
        r"\section{Method} " + "word " * 300, encoding="utf-8"
    )
    extract = org.parent / "exemplar.txt"
    # Everything after the reference list is cut on both sides, so the
    # comparison is body against body.
    extract.write_text(
        "body " * 4000 + "\nReferences\n" + "Hinton et al. " * 900,
        encoding="utf-8",
    )
    org.write_text(
        json.dumps({"exemplars": [
            {"title": "A", "text_extract": str(extract.relative_to(tmp_path))},
            {"title": "B", "text_extract": str(extract.relative_to(tmp_path))},
        ]}),
        encoding="utf-8",
    )

    block = _manuscript_scale_block(tmp_path)
    # 301: the 300 body words plus the section heading.
    assert "301 words of body text" in block
    assert "4,000" in block
    # Two exemplars are not enough to quote a median from.
    assert "typically" not in block


def test_planner_is_told_the_grounding_budget_it_is_held_to() -> None:
    """The Planner was cut off at sixteen tool calls and had everything it had
    done discarded, without ever being told the limit existed. That happened
    413 times in one night across seven campaigns -- 162 in run-07 alone, which
    then spent missions trying to raise a cap it cannot reach.
    """
    import inspect

    from argus_skill.planner import planner

    source = inspect.getsource(planner)
    end = source.index("GROUNDING BUDGET:")
    # The limits are computed just above the sentence that states them.
    stated = source[end - 700 : end + 400]
    # The numbers come from the config that enforces them, never a literal.
    assert "cfg.grounding_max_tool_calls" in stated
    assert "cfg.grounding_max_seconds" in stated
    # And it must say what running out costs, or the number means nothing.
    assert "discards this whole turn" in stated


def _wait_state(**overrides) -> dict:
    state = {"idle_capacity_turn_used": True, "operator_action_required": False,
             "idle_capacity_turn_ts": 0.0}
    state.update(overrides)
    return state


def test_a_wait_only_the_operator_can_end_keeps_asking_for_other_work() -> None:
    """One planning turn is the right budget for a wait that ends by itself.
    A wait that ends only when the operator acts does not end at all overnight:
    run-04 spent fifteen hours on wake_on ["authorization"] with expires_at 0,
    having spent its single turn in the first minute, while its paper sat at
    8,107 words using four of its thirty-one figures. run-05 parked the same
    way on an authentication decision.
    """
    import time
    import types

    from argus_skill.life.supervisor._planning_context import (
        IDLE_BACKOFF_CAP_SECONDS,
        PlanningContextMixin,
    )

    def check(state: dict, *, busy: bool = False) -> bool:
        host = types.SimpleNamespace(
            _nothing_queued_behind_the_wait=lambda: not busy
        )
        return PlanningContextMixin._planner_turn_available_during_wait(host, state)

    # A wait that ends by itself still gets exactly one turn, ever.
    assert check(_wait_state(idle_capacity_turn_used=False))
    assert not check(_wait_state())

    # A wait only the operator can end re-arms on the idle cadence.
    operator = _wait_state(operator_action_required=True,
                           idle_capacity_turn_ts=time.time())
    assert not check(operator), "not a poll: it waits out the backoff first"
    operator["idle_capacity_turn_ts"] = time.time() - IDLE_BACKOFF_CAP_SECONDS - 1
    assert check(operator)

    # Never while the campaign already has work queued behind the wait.
    assert not check(operator, busy=True)
