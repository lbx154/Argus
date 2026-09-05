"""The Planner must be able to see the shape of the whole campaign.

The detailed history window handed to the Planner is only
``_PLANNER_HISTORY_COUNT`` entries. That is right for "what just happened", but
it made the campaign's shape invisible: measured on a real project on this box,
319 missions closed, 296 consecutive planner cycles all returned
``tasks_scheduled``, and exactly one replan was ever requested. The Planner is
asked whether the project is finished or the strategy needs replacing while
being shown three entries.

The tally reports COUNTS ONLY. Reading a pattern into them is the Planner's
judgment; the harness must not turn it into a recommendation.
"""

from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.supervisor._planner_rendering import (
    _TALLY_WINDOW_MISSIONS,
    PlannerRenderingMixin,
)


class _Journal:
    """Mirror of ``EventJournal``'s kind-scoped tails over in-memory entries."""

    def __init__(self, entries):
        self._entries = entries

    def tail_settlements(self, n, *, kinds=None):
        rows = [
            entry for entry in self._entries
            if kinds is None or getattr(entry, "kind", "") in kinds
        ]
        return rows[-n:]

    def tail_kinds(self, n, *, kinds):
        rows = [
            entry for entry in self._entries
            if getattr(entry, "kind", "") in kinds
        ]
        return rows[-n:]


def _entry(
    kind: str,
    title: str = "t",
    summary: str = "s",
    *,
    forward_progress: bool | None = None,
    cost_usd: float = 0.0,
    elapsed_seconds: float = 0.0,
):
    extra: dict = {"elapsed_seconds": elapsed_seconds}
    if forward_progress is not None:
        extra["planner_report"] = {"forward_progress": forward_progress}
    return SimpleNamespace(
        kind=kind,
        title=title,
        summary=summary,
        ts=0.0,
        cost_usd=cost_usd,
        extra=extra,
    )


def _renderer(entries):
    obj = PlannerRenderingMixin()
    obj.memory = SimpleNamespace(journal=_Journal(entries))
    return obj


def test_tally_counts_every_terminal_mission_not_just_the_window() -> None:
    entries = [_entry("mission_complete") for _ in range(40)]
    line = _renderer(entries)._render_campaign_tally()
    assert "40 terminal missions" in line
    assert "complete=40" in line


def test_tally_says_plainly_when_no_replan_has_ever_happened() -> None:
    entries = [_entry("mission_complete") for _ in range(30)]
    line = _renderer(entries)._render_campaign_tally()
    assert "no mission has ever requested a replacement plan" in line


def test_tally_reports_distance_since_the_last_replan() -> None:
    entries = (
        [_entry("mission_complete") for _ in range(5)]
        + [_entry("mission_replan_requested")]
        + [_entry("mission_complete") for _ in range(7)]
    )
    line = _renderer(entries)._render_campaign_tally()
    assert "replan_requested=1" in line
    assert "7 terminal missions since the last replan" in line


def test_tally_is_silent_on_a_fresh_campaign() -> None:
    assert _renderer([])._render_campaign_tally() == ""
    # Non-terminal noise must not fabricate a tally.
    assert _renderer([_entry("budget_pause")])._render_campaign_tally() == ""


def test_tally_states_facts_and_never_a_recommendation() -> None:
    """The harness reports counts; the Planner decides what they mean."""
    entries = [
        _entry("mission_complete", forward_progress=False, cost_usd=9.0, elapsed_seconds=3600)
        for _ in range(200)
    ]
    line = _renderer(entries)._render_campaign_tally().lower()
    for verdict_word in (
        "should", "must", "recommend", "consider", "stuck",
        "failing", "give up", "abandon", "project_done",
    ):
        assert verdict_word not in line, f"tally must not advise: {verdict_word!r}"


def test_tally_is_prepended_to_the_planner_history() -> None:
    entries = [_entry("mission_complete", title="did a thing") for _ in range(12)]
    rendered = _renderer(entries)._render_journal_for_planner()
    assert rendered.splitlines()[0].startswith("Campaign totals")
    # The detailed recency window still follows.
    assert "did a thing" in rendered


def test_tally_survives_a_broken_journal() -> None:
    obj = PlannerRenderingMixin()

    class _Boom:
        def tail_settlements(self, n, *, kinds=None):
            raise RuntimeError("journal unavailable")

    obj.memory = SimpleNamespace(journal=_Boom())
    assert obj._render_campaign_tally() == ""


def test_tally_degrades_its_claims_when_the_window_saturates() -> None:
    """A full window cannot claim to have seen the whole campaign.

    ``no mission has EVER requested a replacement plan`` is an absolute fact.
    Once ``len(missions)`` equals the window, older settlements exist that the
    tally cannot see, so both the headline and the never-replanned claim must
    become window-scoped statements.
    """
    entries = [_entry("mission_complete") for _ in range(_TALLY_WINDOW_MISSIONS)]
    line = _renderer(entries)._render_campaign_tally()
    assert f"last {_TALLY_WINDOW_MISSIONS} terminal missions" in line
    assert "no mission has ever requested a replacement plan" not in line
    assert (
        f"no replacement plan requested in the last {_TALLY_WINDOW_MISSIONS} "
        "terminal missions"
    ) in line


def test_tally_keeps_absolute_claims_below_the_window() -> None:
    entries = [_entry("mission_complete") for _ in range(_TALLY_WINDOW_MISSIONS - 1)]
    line = _renderer(entries)._render_campaign_tally()
    assert f"({_TALLY_WINDOW_MISSIONS - 1} terminal missions)" in line
    assert "no mission has ever requested a replacement plan" in line


def test_tally_shows_a_stalled_objective_behind_locally_complete_missions() -> None:
    """The counts alone cannot express the failure that motivated this.

    A run can end ``mission_complete`` because the round was correct while the
    Reviewer judged that the operator's objective did not move. Four such
    missions read as an unbroken success streak unless that verdict is carried
    up with them.
    """
    entries = [_entry("mission_complete", forward_progress=False) for _ in range(4)]
    line = _renderer(entries)._render_campaign_tally()
    assert "complete=4" in line
    assert "4 of 4 reviewed missions reported no objective-level progress" in line


def test_tally_counts_only_the_missions_a_reviewer_actually_judged() -> None:
    entries = [
        _entry("mission_complete", forward_progress=True),
        _entry("mission_complete", forward_progress=False),
        _entry("mission_complete"),
    ]
    line = _renderer(entries)._render_campaign_tally()
    assert "1 of 2 reviewed missions" in line


def test_tally_is_silent_about_progress_when_every_mission_moved_the_goal() -> None:
    entries = [_entry("mission_complete", forward_progress=True) for _ in range(6)]
    assert "objective-level progress" not in _renderer(entries)._render_campaign_tally()


def test_tally_reports_what_the_campaign_has_already_spent() -> None:
    entries = [
        _entry("mission_complete", cost_usd=120.5, elapsed_seconds=7200)
        for _ in range(2)
    ]
    line = _renderer(entries)._render_campaign_tally()
    assert "cumulative $241.00 over 4.0h" in line


def test_tally_omits_a_price_it_cannot_measure() -> None:
    entries = [_entry("mission_complete") for _ in range(3)]
    assert "cumulative" not in _renderer(entries)._render_campaign_tally()


def test_tally_tolerates_journal_entries_of_any_shape() -> None:
    """Journal payloads come from many emitters and predate these fields."""
    entries = [
        SimpleNamespace(kind="mission_complete"),
        SimpleNamespace(kind="mission_complete", extra="not a mapping", cost_usd=None),
        SimpleNamespace(
            kind="mission_failed",
            extra={"planner_report": "not a mapping", "elapsed_seconds": "soon"},
            cost_usd="free",
        ),
    ]
    line = _renderer(entries)._render_campaign_tally()
    assert "3 terminal missions" in line
    assert "cumulative" not in line
