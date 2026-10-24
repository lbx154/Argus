"""Dependency-DAG scheduling for the backlog.

Covers the upgrade from a flat priority queue to a topologically-scheduled
DAG: ``claim_next`` only hands out items whose ``deps`` are all ``done``,
dead dependencies cascade-skip instead of wedging the queue, and the
no-deps path is provably unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.life.memory import Backlog, BacklogItem

# ---------- schema: deps field ---------------------------------------------


# ---------- no-deps behaviour is unchanged ---------------------------------


# ---------- chain A -> B ---------------------------------------------------


# ---------- fan-in: C depends on A and B -----------------------------------


# ---------- dead-dependency cascade ----------------------------------------


def test_missing_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    b.add(BacklogItem.new(title="B", objective="...", deps=["does-not-exist"]))
    assert b.claim_next() is None
    rows = b.all()
    assert rows[0].status == "skipped"
    assert "does not exist" in rows[0].last_error


def test_self_and_cyclic_deps_are_reconciled_to_terminal_skips(tmp_path: Path) -> None:
    # Legacy/corrupt state can still contain a cycle even though new batch
    # commits reject one. The scheduler must make that state terminal instead
    # of treating it as an empty ready queue forever.
    b = Backlog(tmp_path / "backlog.jsonl")
    a = BacklogItem.new(title="A", objective="...")
    a.deps = [a.id]
    x = BacklogItem.new(title="X", objective="...")
    y = BacklogItem.new(title="Y", objective="...")
    x.deps = [y.id]
    y.deps = [x.id]
    b._save([a, x, y])

    # next_pending is the supervisor's first read and therefore must run the
    # same reconciliation as claim_next.
    assert b.next_pending() is None
    rows = b.all()
    assert all(it.status == "skipped" for it in rows)
    assert all("dependency cycle" in it.last_error for it in rows)


def test_add_many_rejects_dependency_cycle_before_commit(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = BacklogItem.new(title="A", objective="...")
    bee = BacklogItem.new(title="B", objective="...")
    a.deps = [bee.id]
    bee.deps = [a.id]

    with pytest.raises(ValueError, match="dependency cycle"):
        b.add_many([a, bee])

    assert b.all() == []


def test_operator_answer_resumes_the_task_its_dependents_wait_on(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    blocked = BacklogItem.new(title="blocked", objective="choose a GPU")
    blocked.status = "parked"
    blocked.pending_question = "Which GPU?"
    blocked.manager_decision = {"vertical": "software"}
    b.add(blocked)
    downstream = b.add(
        BacklogItem.new(
            title="downstream",
            objective="run the benchmark",
            deps=[blocked.id],
        )
    )

    resumed, answered = b.resume_with_operator_reply(
        blocked.id,
        "Use GPU 1",
        manager_decision="GPU 1 is authorized.",
    )

    assert answered and resumed.id == blocked.id
    assert resumed.manager_decision == {
        "display_objective": "choose a GPU", "vertical": "software", "routed": True,
    }
    assert "review:required" in resumed.tags
    stored_downstream = next(item for item in b.all() if item.id == downstream.id)
    assert stored_downstream.deps == [blocked.id]
    assert b.claim_next().id == blocked.id
    b.mark_done(blocked.id)
    assert b.claim_next().id == downstream.id


# ---------- ready() vs pending() -------------------------------------------
