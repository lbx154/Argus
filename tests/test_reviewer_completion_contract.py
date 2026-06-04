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
from argus_skill.life.memory import BacklogItem, JournalEntry, LifeMemory
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


def _make_supervisor_cfg(tmp_path: Path, **cfg_kwargs) -> LifeSupervisor:
    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(), poll_interval_seconds=0.01, **cfg_kwargs
    )

    class _Sink:
        def handle_event(self, event: dict) -> None:  # noqa: D401
            pass

    class _Runner:
        pass

    return LifeSupervisor(memory=mem, runner=_Runner(), sink=_Sink(), config=cfg)


def test_backlog_metadata_paper_guidance_driven_by_explicit_flag(tmp_path: Path) -> None:
    """Paper-vs-bounded guidance keys on config.paper_mission, not objective text."""
    # An objective whose prose reads exactly like a paper task...
    item = BacklogItem.new(
        title="benchmark stage",
        objective="Work the EMNLP paper benchmark stage and resolve blockers.",
        tags=["scope:bounded"],
    )
    # ...is treated as a plain bounded task when paper_mission is OFF.
    sup_off = _make_supervisor_cfg(tmp_path / "off", paper_mission=False)
    out_off = sup_off._render_backlog_item_metadata(item)
    assert "bounded_task" in out_off
    assert "paper_optimization_task" not in out_off

    # ...and gets the long-horizon paper guidance only when paper_mission is ON,
    # even for a non-papery-looking objective.
    plain = BacklogItem.new(
        title="tune loader", objective="optimize the data loader", tags=["scope:bounded"]
    )
    sup_on = _make_supervisor_cfg(tmp_path / "on", paper_mission=True)
    out_on = sup_on._render_backlog_item_metadata(plain)
    assert "paper_optimization_task" in out_on
    assert "bounded_task" not in out_on


def test_open_ended_is_explicit_flag_not_objective_keywords(tmp_path: Path) -> None:
    """The post-project_done 'continue forever' behavior keys on config.open_ended.

    Previously the supervisor sniffed the objective text for markers like
    '7×24'/'ongoing'/'perpetual'. That keyword classifier is gone: an objective
    full of perpetual-sounding words must NOT implicitly enable open-ended mode,
    and a terse objective must be able to enable it via the flag.
    """
    from argus_skill.life import supervisor as sup_mod

    assert not hasattr(sup_mod, "_objective_is_open_ended")

    perpetual = _make_supervisor_cfg(
        tmp_path / "kw",
        continuous=True,
        continuous_objective="ongoing 7×24 perpetual never-ending self-improvement",
    )
    assert perpetual.config.open_ended is False

    terse = _make_supervisor_cfg(
        tmp_path / "flag",
        continuous=True,
        continuous_objective="ship it",
        open_ended=True,
    )
    assert terse.config.open_ended is True


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


def test_item_is_final_submission_prefers_structured_tag() -> None:
    from argus_skill.life.memory import BacklogItem
    from argus_skill.life.supervisor import (
        LifeSupervisor,
        _legacy_final_submission_marker,
    )

    # Structured scope tag is the primary signal — objective prose is
    # irrelevant when the tag is present.
    tagged = BacklogItem.new(
        title="Prove final submission readiness",
        objective="anything at all",
        tags=["planner", "scope:final_submission"],
    )
    assert LifeSupervisor._item_is_final_submission(tagged) is True

    bounded = BacklogItem.new(
        title="t",
        objective="add a unit test for the parser",
        tags=["planner", "scope:bounded"],
    )
    assert LifeSupervisor._item_is_final_submission(bounded) is False

    # Legacy items (persisted before scope tagging) fall back to the
    # objective-prose marker so resumed daemons don't regress.
    legacy = BacklogItem.new(
        title="t",
        objective="Project-final task. Scope: final_submission. Complete the pipeline.",
        tags=[],
    )
    assert LifeSupervisor._item_is_final_submission(legacy) is True

    # The legacy recognizer keys on the marker, not arbitrary prose.
    assert _legacy_final_submission_marker(
        "## Backlog item metadata\n- planner_scope: final_submission"
    ) is True
    assert _legacy_final_submission_marker(
        "Bounded task: add a unit test for the parser."
    ) is False
