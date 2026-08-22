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


def test_cheapest_does_not_mean_too_small_to_measure() -> None:
    """Three campaigns read 'cheapest faithful run' as 'fewest examples'.

    They claimed a win of three examples on 120 and of one on 48 -- around half
    a standard error, which no reviewer reads as a result. The word had to be
    told what it does not mean.
    """
    from argus_skill.verticals.research.stages import _PLANNER_RESEARCH_ORCHESTRATION

    planner = _PLANNER_RESEARCH_ORCHESTRATION.lower()
    assert "cheapest means no redundant condition" in planner
    assert "never too few examples to see the margin" in planner
    # The sizing bar stays the campaign's own declared margin, not a fixed n.
    assert "spread of your own repeats" in planner


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
