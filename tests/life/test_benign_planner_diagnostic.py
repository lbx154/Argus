"""A diagnostic that reports no failure must not become one in the agent's memory.

Observed on every run tonight
(/tmp/argus-night/home/projects/e8d2340c8962/events.jsonl event #10, and the
same at #10 of f9a27b1c16a7): the first planning cycle emitted

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

from argus_skill.core.event_catalog import EventType
from argus_skill.life.memory import EventJournal


def _entry(**row):
    payload = {"type": EventType.LIFE_PLANNER_ERROR, "cycle": 1, **row}
    return EventJournal._entry_from_event(payload)


def test_a_real_planner_error_is_still_journalled() -> None:
    entry = _entry(error="planner verdict outbox write failed: OSError: disk full")

    assert entry is not None
    assert entry.kind == "planner_error"


def test_a_benign_diagnostic_is_kept_out_of_the_journal() -> None:
    entry = _entry(
        error="discarded stale planner verdict outbox after semantic state change",
        benign=True,
    )

    assert entry is None


def test_the_flag_is_structural_not_textual() -> None:
    """The same message without the flag is still journalled.

    Matching on the wording would make this projection guess at the emitter's
    intent, which is exactly the kind of second-guessing the harness must not do.
    """
    entry = _entry(
        error="discarded stale planner verdict outbox after semantic state change"
    )

    assert entry is not None and entry.kind == "planner_error"


def test_the_discard_marks_itself_benign() -> None:
    """The emitter and the projection have to agree, or the fix is inert."""
    import inspect

    from argus_skill.life.supervisor import _core

    source = inspect.getsource(_core.LifeSupervisor._retry_pending_planner_verdict)
    discard = source.index("discarded stale planner verdict outbox")
    assert '"benign": True' in source[discard : discard + 900]
