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
