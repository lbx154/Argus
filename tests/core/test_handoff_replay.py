"""Real decisions from exp-dogfood3..7, replayed as the forms that replace their text.

Each fixture row is one recorded call, read by the retired text readers and
written as the ``hand_off`` payload it means (design/replay/to_payloads.py);
rows the old readers misread are corrected by hand and marked ``old_defect``.
Every one must still be expressible, and everything the operator reads must
be plain text, so a tightened schema or a leaking protocol word fails here.
"""
from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

import pytest

from argus.core.handoff import HandOffActions
from argus.manager.stage_decider import stage_check
from argus.planner.planner import _plan

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "handoff_replay"
ROWS = [
    pytest.param(path.stem, json.loads(line), id=f"{path.stem}-{json.loads(line)['call_id']}")
    for path in sorted(FIXTURES.glob("*.jsonl"))
    for line in path.read_text(encoding="utf-8").splitlines()
]
_PROTOCOL = re.compile(r"^\s*(?:→|->)|\bDecision:|^[A-Z_]{3,}=", re.MULTILINE)


def _shown(payload: dict) -> list[str]:
    """Every string the operator reads from one handoff."""
    ask = payload.get("ask", {})
    return [
        payload.get("summary", ""), ask.get("question", ""),
        *(text for option in ask.get("options", ()) for text in option.values()),
        *(text for row in payload.get("assumptions", ()) for text in row.values()),
    ]


@pytest.mark.parametrize(("form", "row"), ROWS)
def test_a_recorded_decision_fills_its_form(form: str, row: dict) -> None:
    actions = HandOffActions(form, operator_available=True)

    actions.dispatch("hand_off", row["payload"])

    handoff = actions.handoff
    assert handoff is not None and handoff.to == row["payload"]["to"]
    assert len(handoff.options) == len(row["payload"].get("ask", {}).get("options", ()))
    for text in _shown(row["payload"]):
        assert not _PROTOCOL.search(text), text
        assert text.count("**") % 2 == 0 and text.count("`") % 2 == 0, text
        assert "CHECKPOINT.md" not in text, text


@pytest.mark.parametrize(("form", "row"), [param for param in ROWS if param.values[0] == "plan"])
def test_the_host_reads_a_recorded_plan_as_the_text_readers_did(form: str, row: dict) -> None:
    actions = HandOffActions(form, check=partial(_plan, open_ended=False, stages=()), operator_available=True)

    actions.dispatch("hand_off", row["payload"])

    verdict = actions.handoff.value
    assert {
        "project_done": verdict.project_done, "handoff": verdict.handoff,
        "tasks": [task.title for task in verdict.new_tasks], "retire": [list(row) for row in verdict.retire_tasks],
        "settled": list(verdict.settled_instructions), "advance_to_stage": verdict.advance_to_stage,
        "plan_update": bool(verdict.plan_update),
    } == row["old_reading"]


def test_the_replay_covers_every_form_so_far() -> None:
    forms = {form for form, _ in (param.values for param in ROWS)}
    assert forms == {"engineer", "review", "route", "supervise", "plan", "stage"}
    assert sum(row.get("old_defect", False) for _, row in (param.values for param in ROWS)) == 3


@pytest.mark.parametrize("row", [row for form, row in (param.values for param in ROWS)
                                 if form == "stage" and not row.get("old_defect")])
def test_the_host_reads_a_recorded_stage_decision_as_the_text_reader_did(row: dict) -> None:
    actions = HandOffActions("stage", operator_available=True, check=partial(
        stage_check, current_stage=row["stage"], stage_order=[row["stage"]], allow_done=False,
    ))

    actions.dispatch("hand_off", row["payload"])

    decision = actions.handoff.value
    assert (decision.action, decision.target_stage) == (row["old"]["action"], row["old"]["target_stage"])
