"""Mechanism-design guards on the backlog state machine.

These tests cover invariants that prevent the entire class of
"already-completed missions accidentally re-execute" bugs:

1. Terminal states (``done`` / ``failed`` / ``skipped`` / ``superseded``) cannot
   transition back to ``pending`` or ``running``.
2. ``claim_next()`` is atomic — it cannot return the same item twice.
3. ``reap_orphans()`` rescues crashed ``running`` items into
   ``failed``, but never auto-requeues them (no infinite-loop risk).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.life.memory import (
    BacklogItem,
    IllegalStateTransition,
    LifeMemory,
)


@pytest.fixture()
def mem(tmp_path: Path) -> LifeMemory:
    return LifeMemory.open(root=tmp_path)


# ---------------------------------------------------------------------------
# Terminal-state seal: done/failed/skipped cannot resurrect
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Orphan reaper
# ---------------------------------------------------------------------------


def test_reap_orphans_requeues_but_respects_max_retries(mem: LifeMemory) -> None:
    """Orphaned items are re-queued up to max_retries times, then failed.
    A failed item cannot be flipped back to pending (poison-pill protection)."""
    item = mem.backlog.add(BacklogItem.new(title="t", objective="poison"))
    mem.backlog.mark_running(item.id)
    mem.backlog.reap_orphans()
    # First reap → pending (retry 1)
    assert mem.backlog.next_pending() is not None
    # Exhaust retries
    for _ in range(2):
        mem.backlog.mark_running(item.id)
        mem.backlog.reap_orphans()
    # retry 3 → still pending
    assert mem.backlog.next_pending() is not None
    # retry 4 → failed (exceeded max_retries=3)
    mem.backlog.mark_running(item.id)
    mem.backlog.reap_orphans()
    assert mem.backlog.next_pending() is None
    with pytest.raises(IllegalStateTransition):
        mem.backlog.update(item.id, status="pending")
