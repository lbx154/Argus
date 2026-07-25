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

from argus_skill.life.supervisor._planner_rendering import PlannerRenderingMixin


class _Journal:
    def __init__(self, entries):
        self._entries = entries

    def tail(self, n):
        return self._entries[-n:]


def _entry(kind: str, title: str = "t", summary: str = "s"):
    return SimpleNamespace(kind=kind, title=title, summary=summary, ts=0.0, extra={})


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
    entries = [_entry("mission_complete") for _ in range(200)]
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
        def tail(self, n):
            raise RuntimeError("journal unavailable")

    obj.memory = SimpleNamespace(journal=_Boom())
    assert obj._render_campaign_tally() == ""
