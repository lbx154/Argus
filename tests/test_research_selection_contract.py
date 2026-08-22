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
    # And a negative result is only publishable if it kills something.
    assert "naming the belief it kills" in draft


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


def test_rigour_does_not_excuse_a_missing_argument() -> None:
    """run-06 produced the most careful measurement in the portfolio -- four
    resampling schemes, a predeclared win rule, a positive control with
    specificity 1.00 -- inside a 1,948-word manuscript whose introduction is 160
    words. For a negative result that is fatal: its whole value is in
    establishing that the field would have bet the other way, and no amount of
    resampling supplies that.
    """
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    draft = " ".join(
        next(i for i in STAGE_CHECKLISTS["draft"] if i.id == "draft.tex").statement.split()
    )
    assert "give the argument the room the measurement got" in draft
    assert "would have bet the other way" in draft
    assert "Rigour buys the right to be believed" in draft


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
