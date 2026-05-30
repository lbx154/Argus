"""Tests for the reviewer-driven final-submission completion contract.

This contract replaces the retired hardcoded EMNLP validator gate
(``validate_full_emnlp_readiness`` and friends). Whole-project completion
is now certified by the L2 reviewer's full-pipeline checklist verdict:

* ``ReviewDecision.final_submission_certified`` is True only for a ``done``
  verdict scoped to ``final_submission`` whose checklist is non-empty and
  every item is satisfied with concrete evidence (fail-closed).
* The reviewer JSON parser must parse ``scope`` / ``checklist`` fail-closed.
* ``LifeSupervisor._journal_has_full_emnlp_gate_success`` reads the journal
  for a ``mission_complete`` entry stamped ``final_submission_certified``,
  never a validator call.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.reviewer import _find_decision_in_messages
from argus_skill.life.memory import JournalEntry, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


# ---------------------------------------------------------------------------
# ReviewDecision.final_submission_certified
# ---------------------------------------------------------------------------

def _decision(**kw) -> ReviewDecision:
    base = dict(
        status="done",
        confidence=1.0,
        reason="ok",
        next_action="",
        round_summary_markdown="# x",
        completion_summary_markdown="done",
    )
    base.update(kw)
    return ReviewDecision(**base)


def test_certified_when_all_items_satisfied_with_evidence() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[
            {"item": "experiments", "satisfied": True, "evidence": "pytest 12 passed"},
            {"item": "paper", "satisfied": True, "evidence": "main.pdf 8 pages"},
        ],
    )
    assert d.final_submission_certified is True


def test_not_certified_when_scope_missing() -> None:
    d = _decision(
        scope="",
        checklist=[{"item": "a", "satisfied": True, "evidence": "e"}],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_checklist_empty() -> None:
    d = _decision(scope="final_submission", checklist=[])
    assert d.final_submission_certified is False


def test_not_certified_when_item_unsatisfied() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[
            {"item": "a", "satisfied": True, "evidence": "e"},
            {"item": "b", "satisfied": False, "evidence": ""},
        ],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_evidence_blank() -> None:
    d = _decision(
        scope="final_submission",
        checklist=[{"item": "a", "satisfied": True, "evidence": "   "}],
    )
    assert d.final_submission_certified is False


def test_not_certified_when_status_continue() -> None:
    d = _decision(
        status="continue",
        scope="final_submission",
        checklist=[{"item": "a", "satisfied": True, "evidence": "e"}],
    )
    assert d.final_submission_certified is False


# ---------------------------------------------------------------------------
# Reviewer JSON parser: scope / checklist (fail-closed)
# ---------------------------------------------------------------------------

def _parse(payload: dict) -> ReviewDecision | None:
    return _find_decision_in_messages([json.dumps(payload)])


def test_parser_reads_scope_and_checklist() -> None:
    decision = _parse({
        "status": "done",
        "confidence": 0.95,
        "reason": "all items verified",
        "next_action": "",
        "round_summary_markdown": "# Review\n- ok",
        "completion_summary_markdown": "complete",
        "scope": "final_submission",
        "checklist": [
            {"item": "run", "satisfied": True, "evidence": "stdout shows acc=0.9"},
        ],
    })
    assert decision is not None
    assert decision.scope == "final_submission"
    assert decision.checklist == [
        {"item": "run", "satisfied": True, "evidence": "stdout shows acc=0.9"}
    ]
    assert decision.final_submission_certified is True


def test_parser_defaults_when_scope_checklist_absent() -> None:
    decision = _parse({
        "status": "done",
        "confidence": 0.9,
        "reason": "bounded task done",
        "next_action": "",
        "round_summary_markdown": "# Review\n- ok",
        "completion_summary_markdown": "done",
    })
    assert decision is not None
    assert decision.scope == ""
    assert decision.checklist == []
    assert decision.final_submission_certified is False


def test_parser_drops_malformed_scope() -> None:
    decision = _parse({
        "status": "done",
        "confidence": 0.9,
        "reason": "x",
        "next_action": "",
        "round_summary_markdown": "# Review",
        "completion_summary_markdown": "done",
        "scope": "garbage",
        "checklist": "not-a-list",
    })
    assert decision is not None
    assert decision.scope == ""
    assert decision.checklist == []


# ---------------------------------------------------------------------------
# LifeSupervisor journal gate: reviewer certification, not validators
# ---------------------------------------------------------------------------

def _make_supervisor(tmp_path: Path) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(budget=LifeBudget(), poll_interval_seconds=0.01)

    class _Sink:
        def handle_event(self, event: dict) -> None:  # noqa: D401
            pass

    class _Runner:
        pass

    return LifeSupervisor(memory=mem, runner=_Runner(), sink=_Sink(), config=cfg)


def test_journal_gate_true_only_with_certified_entry(tmp_path: Path) -> None:
    sup = _make_supervisor(tmp_path)
    # No certified entry yet.
    assert sup._journal_has_full_emnlp_gate_success() is False

    # A completed mission that was NOT certified must not pass the gate.
    sup.memory.journal.append(JournalEntry.new(
        kind="mission_complete",
        title="bounded task",
        summary="status=done",
        extra={"final_submission_certified": False},
    ))
    assert sup._journal_has_full_emnlp_gate_success() is False

    # A certified final-submission entry passes the gate.
    sup.memory.journal.append(JournalEntry.new(
        kind="mission_complete",
        title="final submission",
        summary="status=done",
        extra={"final_submission_certified": True},
    ))
    assert sup._journal_has_full_emnlp_gate_success() is True


def test_journal_gate_ignores_stale_validator_text(tmp_path: Path) -> None:
    """Legacy journal prose mentioning the old gate must NOT certify."""
    sup = _make_supervisor(tmp_path)
    sup.memory.journal.append(JournalEntry.new(
        kind="mission_complete",
        title="legacy",
        summary="validate-full-emnlp exited 0",
        extra={"completion_summary": "validate-full-emnlp exited 0"},
    ))
    assert sup._journal_has_full_emnlp_gate_success() is False


def test_is_emnlp_finalization_objective_keys_on_scope() -> None:
    from argus_skill.life.supervisor import _is_emnlp_finalization_objective

    assert _is_emnlp_finalization_objective(
        "Project-final task. Scope: final_submission. Complete the pipeline."
    ) is True
    assert _is_emnlp_finalization_objective(
        "## Backlog item metadata\n- planner_scope: final_submission"
    ) is True
    assert _is_emnlp_finalization_objective(
        "Bounded task: add a unit test for the parser."
    ) is False
