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

from argus.life.supervisor._planner_rendering import (
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
    cost_usd: float = 0.0,
    elapsed_seconds: float = 0.0,
):
    extra: dict = {"elapsed_seconds": elapsed_seconds}
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


def test_tally_reports_distance_since_the_last_replan() -> None:
    entries = (
        [_entry("mission_complete") for _ in range(5)]
        + [_entry("mission_replan_requested")]
        + [_entry("mission_complete") for _ in range(7)]
    )
    line = _renderer(entries)._render_campaign_tally()
    assert "replan_requested=1" in line
    assert "7 terminal missions since the last replan" in line


def test_tally_survives_a_broken_journal() -> None:
    obj = PlannerRenderingMixin()

    class _Boom:
        def tail_settlements(self, n, *, kinds=None):
            raise RuntimeError("journal unavailable")

    obj.memory = SimpleNamespace(journal=_Boom())
    assert obj._render_campaign_tally() == ""


def test_tally_reports_what_the_campaign_has_already_spent() -> None:
    entries = [
        _entry("mission_complete", cost_usd=120.5, elapsed_seconds=7200)
        for _ in range(2)
    ]
    line = _renderer(entries)._render_campaign_tally()
    assert "cumulative $241.00 over 4.0h" in line
