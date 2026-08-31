"""Dependency-DAG scheduling for the backlog.

Covers the upgrade from a flat priority queue to a topologically-scheduled
DAG: ``claim_next`` only hands out items whose ``deps`` are all ``done``,
dead dependencies cascade-skip instead of wedging the queue, and the
no-deps path is provably unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.life.memory import Backlog, BacklogItem

# ---------- schema: deps field ---------------------------------------------

def test_new_defaults_to_empty_deps() -> None:
    it = BacklogItem.new(title="t", objective="o")
    assert it.deps == []


def test_new_accepts_deps() -> None:
    it = BacklogItem.new(title="t", objective="o", deps=["a", "b"])
    assert it.deps == ["a", "b"]
    # ``new`` must copy, not alias, the caller's list.
    src = ["x"]
    it2 = BacklogItem.new(title="t", objective="o", deps=src)
    src.append("y")
    assert it2.deps == ["x"]


def test_deps_roundtrip_through_jsonable() -> None:
    it = BacklogItem.new(title="t", objective="o", deps=["dep1", "dep2"])
    restored = BacklogItem.from_jsonable(it.to_jsonable())
    assert restored.deps == ["dep1", "dep2"]


def test_existing_id_does_not_generate_a_replacement(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.life.memory.uuid.uuid4",
        lambda: pytest.fail("existing IDs must not consume a random UUID"),
    )

    restored = BacklogItem.from_jsonable({"id": "stable", "objective": "o"})

    assert restored.id == "stable"


def test_legacy_row_without_deps_loads_as_no_deps() -> None:
    # A pre-DAG jsonl row has no "deps" key at all.
    legacy = {
        "id": "abc123",
        "ts": 1700000000.0,
        "title": "old",
        "objective": "old objective",
        "status": "pending",
        "priority": 100,
        "tags": [],
        "notes": "",
    }
    restored = BacklogItem.from_jsonable(legacy)
    assert restored.deps == []


# ---------- no-deps behaviour is unchanged ---------------------------------

def test_claim_next_no_deps_matches_priority_order(tmp_path: Path) -> None:
    # Identical to the pre-DAG flat backlog: priority then ts.
    b = Backlog(tmp_path / "backlog.jsonl")
    b.add(BacklogItem.new(title="low", objective="...", priority=200))
    hi = b.add(BacklogItem.new(title="hi", objective="...", priority=10))
    b.add(BacklogItem.new(title="mid", objective="...", priority=100))

    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.id == hi.id
    assert claimed.status == "running"
    # next_pending mirrors the ready head; hi is now running, so mid is next.
    head = b.next_pending()
    assert head is not None and head.title == "mid"


def test_claim_next_empty_backlog_is_none(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    assert b.claim_next() is None
    assert b.next_pending() is None


# ---------- chain A -> B ---------------------------------------------------

def test_chain_dependency_blocks_until_done(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # First claim must be A; B is gated behind A.
    first = b.claim_next()
    assert first is not None and first.id == a.id
    # B is not ready while A is only running.
    assert b.next_pending() is None
    assert b.claim_next() is None

    # Finish A; now B becomes claimable.
    b.mark_done(a.id)
    second = b.claim_next()
    assert second is not None and second.id == bee.id


# ---------- fan-in: C depends on A and B -----------------------------------

def test_fan_in_waits_for_all_deps(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="..."))
    c = b.add(BacklogItem.new(title="C", objective="...", deps=[a.id, bee.id]))

    # Two claims hand out A and B (order doesn't matter); C is gated.
    claimed1 = b.claim_next()
    claimed2 = b.claim_next()
    assert claimed1 is not None and claimed2 is not None
    assert {claimed1.id, claimed2.id} == {a.id, bee.id}
    assert b.claim_next() is None  # C still blocked (no dep done)

    # One dep done is not enough.
    b.mark_done(a.id)
    assert b.claim_next() is None

    # Both deps done -> C is claimable.
    b.mark_done(bee.id)
    third = b.claim_next()
    assert third is not None and third.id == c.id


# ---------- dead-dependency cascade ----------------------------------------

def test_failed_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # A fails -> B can never satisfy its deps.
    b.mark_failed(a.id, error="boom")
    # claim_next runs the cascade: B is marked skipped, and nothing ready.
    assert b.claim_next() is None

    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "skipped"
    assert "did not complete" in rows["B"].last_error
    assert a.id in rows["B"].last_error
    # B is no longer pending, so it cannot be claimed.
    assert b.next_pending() is None


def test_skipped_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))
    b.update(a.id, status="skipped")

    assert b.claim_next() is None
    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "skipped"


def test_missing_dependency_cascade_skips_dependent(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    b.add(BacklogItem.new(title="B", objective="...", deps=["does-not-exist"]))
    assert b.claim_next() is None
    rows = b.all()
    assert rows[0].status == "skipped"
    assert "does not exist" in rows[0].last_error


def test_cascade_does_not_touch_items_with_live_deps(tmp_path: Path) -> None:
    # A still pending (not terminal) -> B stays pending, not cascaded.
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="...", priority=10))
    b.add(BacklogItem.new(title="B", objective="...", priority=5, deps=[a.id]))

    # B has higher priority but is blocked; A is claimed first.
    claimed = b.claim_next()
    assert claimed is not None and claimed.id == a.id
    # B is untouched, still pending.
    rows = {it.title: it for it in b.all()}
    assert rows["B"].status == "pending"
    assert rows["B"].last_error == ""


def test_pending_operator_question_is_not_claimable_without_approval(
    tmp_path: Path,
) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    statement_lock = BacklogItem.new(
        title="statement-lock",
        objective="Lock the statement only after operator approval.",
        manager_decision={"routed": True, "vertical": "argus_maintenance"},
    )
    statement_lock.pending_question = "Approve this statement before locking it?"
    b.add(statement_lock)

    assert b.ready() == []
    assert b.next_pending() is None
    assert b.claim_next() is None

    stored = b.all()[0]
    assert stored.status == "pending"
    assert stored.pending_question == "Approve this statement before locking it?"


def test_operator_blocked_dependency_keeps_downstream_unrunnable(
    tmp_path: Path,
) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    statement_lock = BacklogItem.new(
        title="statement-lock",
        objective="Lock the statement only after operator approval.",
        priority=10,
        manager_decision={"routed": True, "vertical": "argus_maintenance"},
    )
    statement_lock.pending_question = "Approve this statement before locking it?"
    lock = b.add(statement_lock)
    exact_search = b.add(
        BacklogItem.new(
            title="exact-search",
            objective="Search only after the statement is locked.",
            priority=1,
            deps=[lock.id],
            manager_decision={"routed": True, "vertical": "argus_maintenance"},
        )
    )
    b.add(
        BacklogItem.new(
            title="formalization",
            objective="Formalize only after exact search.",
            priority=2,
            deps=[exact_search.id],
            manager_decision={"routed": True, "vertical": "argus_maintenance"},
        )
    )

    assert b.ready() == []
    assert b.next_pending() is None
    assert b.claim_next() is None

    rows = {it.title: it for it in b.all()}
    assert rows["statement-lock"].status == "pending"
    assert rows["exact-search"].status == "pending"
    assert rows["formalization"].status == "pending"
    assert rows["exact-search"].last_error == ""
    assert rows["formalization"].last_error == ""


def test_operator_pause_dependency_remains_blocked_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backlog.jsonl"
    backlog = Backlog(path)
    approval = BacklogItem.new(
        title="operator approval",
        objective="Wait for explicit operator approval.",
        manager_decision={"routed": True, "vertical": "software"},
    )
    approval.status = "paused_operator"
    approval.pending_question = "Approve the next phase?"
    parent = backlog.add(approval)
    backlog.add(
        BacklogItem.new(
            title="dependent work",
            objective="Run only after approval.",
            deps=[parent.id],
            manager_decision={"routed": True, "vertical": "software"},
        )
    )

    reopened = Backlog(path)

    assert reopened.next_pending() is None
    assert reopened.claim_next() is None
    assert {item.title: item.status for item in reopened.all()} == {
        "operator approval": "paused_operator",
        "dependent work": "pending",
    }


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


def test_next_pending_reconciles_dead_dependency_without_claim(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    failed = b.add(BacklogItem.new(title="failed", objective="..."))
    dependent = b.add(
        BacklogItem.new(title="dependent", objective="...", deps=[failed.id])
    )
    b.mark_failed(failed.id, error="boom")

    assert b.next_pending() is None
    stored = next(item for item in b.all() if item.id == dependent.id)
    assert stored.status == "skipped"
    assert failed.id in stored.last_error


def test_operator_answer_rewires_pending_dependents(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    blocked = BacklogItem.new(title="blocked", objective="choose a GPU")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU?"
    blocked.execution_workdir = "/private/framework"
    blocked.authorization_id = "maintenance-auth"
    blocked.authorization_action = "repair"
    blocked.manager_decision = {
        "routed": True,
        "vertical": "software",
        "workflow_mode": "staged",
    }
    b.add(blocked)
    downstream = b.add(
        BacklogItem.new(
            title="downstream",
            objective="run the benchmark",
            deps=[blocked.id],
        )
    )

    original, continuation = b.continue_with_operator_reply(
        blocked.id,
        "Use GPU 1",
        manager_decision="GPU 1 is authorized.",
    )

    assert original is not None and continuation is not None
    assert continuation.execution_workdir == "/private/framework"
    assert continuation.authorization_id == "maintenance-auth"
    assert continuation.authorization_action == "repair"
    assert continuation.manager_decision == blocked.manager_decision
    assert "review:required" in continuation.tags
    stored_downstream = next(item for item in b.all() if item.id == downstream.id)
    assert stored_downstream.deps == [continuation.id]
    assert b.claim_next().id == continuation.id
    b.mark_done(continuation.id)
    assert b.claim_next().id == downstream.id


# ---------- ready() vs pending() -------------------------------------------

def test_pending_lists_blocked_but_ready_does_not(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="A", objective="..."))
    bee = b.add(BacklogItem.new(title="B", objective="...", deps=[a.id]))

    # pending() shows both (display/status); ready() shows only A.
    assert {it.id for it in b.pending()} == {a.id, bee.id}
    assert [it.id for it in b.ready()] == [a.id]
