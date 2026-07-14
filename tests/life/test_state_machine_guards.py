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

from argus_skill.life.memory import (
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

@pytest.mark.parametrize("terminal", ["done", "failed", "skipped", "superseded"])
@pytest.mark.parametrize("attempt", ["pending", "running"])
def test_terminal_status_cannot_transition_back(
    mem: LifeMemory, terminal: str, attempt: str
) -> None:
    item = mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    if terminal == "done":
        mem.backlog.mark_done(item.id)
    elif terminal == "failed":
        mem.backlog.mark_failed(item.id, error="boom")
    elif terminal == "skipped":
        mem.backlog.update(item.id, status="skipped")
    else:
        mem.backlog.update(
            item.id,
            status="superseded",
            superseded_by_plan_id="plan-2",
            superseded_reason="replacement plan",
        )

    with pytest.raises(IllegalStateTransition):
        mem.backlog.update(item.id, status=attempt)

    # And the on-disk row must NOT have been mutated.
    reloaded = next(it for it in mem.backlog.all() if it.id == item.id)
    assert reloaded.status == terminal


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------

def test_claim_next_flips_pending_to_running_in_one_step(mem: LifeMemory) -> None:
    item = mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    claimed = mem.backlog.claim_next()
    assert claimed is not None
    assert claimed.id == item.id
    assert claimed.status == "running"
    assert claimed.started_ts is not None
    # On-disk row reflects it.
    reloaded = next(it for it in mem.backlog.all() if it.id == item.id)
    assert reloaded.status == "running"


def test_claim_next_returns_none_when_nothing_pending(mem: LifeMemory) -> None:
    assert mem.backlog.claim_next() is None
    item = mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    mem.backlog.mark_done(item.id)
    assert mem.backlog.claim_next() is None


def test_claim_next_picks_priority_then_ts(mem: LifeMemory) -> None:
    older_lo = mem.backlog.add(BacklogItem.new(title="a", objective="a", priority=10))
    newer_lo = mem.backlog.add(BacklogItem.new(title="b", objective="b", priority=10))
    high_priority = mem.backlog.add(BacklogItem.new(title="c", objective="c", priority=0))

    claimed = mem.backlog.claim_next()
    assert claimed is not None
    assert claimed.id == high_priority.id

    claimed2 = mem.backlog.claim_next()
    assert claimed2 is not None
    assert claimed2.id == older_lo.id

    claimed3 = mem.backlog.claim_next()
    assert claimed3 is not None
    assert claimed3.id == newer_lo.id

    assert mem.backlog.claim_next() is None


def test_claim_then_complete_then_no_resurrect(mem: LifeMemory) -> None:
    """End-to-end: an item that has been claimed and completed cannot be re-claimed."""
    mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    claimed = mem.backlog.claim_next()
    assert claimed is not None
    mem.backlog.mark_done(claimed.id)
    assert mem.backlog.claim_next() is None
    # Even a manual update attempt is blocked.
    with pytest.raises(IllegalStateTransition):
        mem.backlog.update(claimed.id, status="pending")


# ---------------------------------------------------------------------------
# Orphan reaper
# ---------------------------------------------------------------------------

def test_reap_orphans_requeues_on_first_retry(mem: LifeMemory) -> None:
    item = mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    mem.backlog.mark_running(item.id)

    reaped = mem.backlog.reap_orphans()
    assert len(reaped) == 1
    assert reaped[0].id == item.id
    assert reaped[0].status == "pending"  # re-queued, not failed
    assert reaped[0].orphan_retries == 1
    assert "orphan" in reaped[0].last_error.lower()


def test_reap_orphans_fails_after_max_retries(mem: LifeMemory) -> None:
    item = mem.backlog.add(BacklogItem.new(title="t", objective="o"))
    # Simulate 3 prior orphan recoveries
    for _ in range(3):
        mem.backlog.mark_running(item.id)
        reaped = mem.backlog.reap_orphans()
        assert reaped[0].status == "pending"
    # 4th time → exceeds max_retries=3 → failed
    mem.backlog.mark_running(item.id)
    reaped = mem.backlog.reap_orphans()
    assert reaped[0].status == "failed"
    assert reaped[0].orphan_retries == 4


def test_reap_orphans_does_not_touch_pending_or_terminal(mem: LifeMemory) -> None:
    p = mem.backlog.add(BacklogItem.new(title="p", objective="p"))
    d = mem.backlog.add(BacklogItem.new(title="d", objective="d"))
    mem.backlog.mark_done(d.id)
    f = mem.backlog.add(BacklogItem.new(title="f", objective="f"))
    mem.backlog.mark_failed(f.id, error="prior")

    assert mem.backlog.reap_orphans() == []
    statuses = {it.id: it.status for it in mem.backlog.all()}
    assert statuses[p.id] == "pending"
    assert statuses[d.id] == "done"
    assert statuses[f.id] == "failed"


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
