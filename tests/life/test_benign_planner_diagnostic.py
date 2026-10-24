"""A diagnostic that reports no failure must not become one in the agent's memory.

Observed repeatedly in captured project event logs: the first planning cycle
emitted

    life.planner.error — discarded stale planner verdict outbox after
    semantic state change

Nothing had failed. The Manager writing PIPELINE_STATE.json changes the project's
semantic state, so a verdict queued against the old state is correctly dropped.

But LIFE_PLANNER_ERROR is a journalled type, and the journal is what
`render_prelude` feeds back to the Planner as memory context. So every run wrote
a `planner_error` into the Planner's own history describing a failure that never
happened — the agent was reading an invented one each time it started.

The fix is a structural flag from the emitter, not a keyword match on the
message here: the emitter knows whether its own event was an error, and this
projection does not.
"""

from __future__ import annotations

from argus.core.event_catalog import EventType
from argus.life.memory import EventJournal


def _entry(**row):
    payload = {"type": EventType.LIFE_PLANNER_ERROR, "cycle": 1, **row}
    return EventJournal._entry_from_event(payload)


def test_a_benign_diagnostic_is_kept_out_of_the_journal() -> None:
    entry = _entry(
        error="discarded stale planner verdict outbox after semantic state change",
        benign=True,
    )

    assert entry is None
