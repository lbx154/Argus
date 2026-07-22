"""Regression tests for ``LifeSupervisor._render_checklist_feedback``.

Root cause this pins: commit d563cb2 dropped the method's ``def`` signature,
leaving the body as dead code after ``_render_step_back``'s ``return``. The file
still imported (unreachable code after ``return`` is valid), but the method did
not exist on ``LifeSupervisor``. Whenever a reviewer emitted ``checklist_feedback``
and a planner cycle rendered the journal, ``_render_journal_for_planner`` raised
``AttributeError: 'LifeSupervisor' object has no attribute
'_render_checklist_feedback'`` → ``_plan_next_work`` → ``run`` raised → the
daemon wedged in ``run_forever`` ("drain pass raised; sleeping and retrying").
"""
from __future__ import annotations

import types

from argus_skill.life.supervisor import LifeSupervisor

_GOOD_FB = {
    "stage": "run",
    "summary": "the run-stage checklist demands real data that is not yet released",
    "items": [
        {
            "id": "run.baseline",
            "problem": "requires official data that is unavailable pre-release",
            "suggested_fix": "split into a synthetic-data scaffold item now plus a real-data item gated on release",
        },
        {
            "id": "run.coverage",
            "problem": "demands coverage that cannot be measured without the dataset",
            "suggested_fix": "defer to phase-2",
        },
    ],
}


def test_method_exists_on_supervisor():
    # The bug was a missing method definition → AttributeError at call time.
    # This assertion fails on the buggy tree and passes once the def is restored.
    assert hasattr(LifeSupervisor, "_render_checklist_feedback")


def test_render_shows_stage_summary_and_items():
    rendered = LifeSupervisor._render_checklist_feedback(_GOOD_FB)
    assert "CHECKLIST_FEEDBACK" in rendered
    assert "stage=run" in rendered
    assert "the run-stage checklist demands real data" in rendered
    assert "run.baseline" in rendered
    assert "requires official data that is unavailable pre-release" in rendered
    assert "split into a synthetic-data scaffold item now" in rendered


def test_empty_feedback_returns_blank():
    assert LifeSupervisor._render_checklist_feedback({}) == ""
    # Header-only (no usable signal) also renders blank.
    assert (
        LifeSupervisor._render_checklist_feedback(
            {"stage": "", "summary": "", "items": []}
        )
        == ""
    )


def test_items_without_problem_are_dropped_fail_soft():
    fb = {
        "stage": "plan",
        "summary": "",
        "items": [
            {"id": "x", "problem": "", "suggested_fix": "should-be-dropped"},
            {"id": "y", "problem": "a real defect", "suggested_fix": ""},
        ],
    }
    rendered = LifeSupervisor._render_checklist_feedback(fb)
    assert "a real defect" in rendered
    assert "should-be-dropped" not in rendered


def test_render_journal_for_planner_does_not_raise_with_feedback():
    """The actual wedge scenario: a ``mission_complete`` journal entry carrying
    ``checklist_feedback`` must render without raising AttributeError."""

    entry = types.SimpleNamespace(
        kind="mission_complete",
        ts=0.0,
        title="t",
        summary="s",
        extra={"checklist_feedback": _GOOD_FB},
    )
    journal = types.SimpleNamespace(tail=lambda n: [entry])
    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.memory = types.SimpleNamespace(journal=journal)

    out = sup._render_journal_for_planner()  # must NOT raise
    assert "CHECKLIST_FEEDBACK" in out
    assert "run.baseline" in out
