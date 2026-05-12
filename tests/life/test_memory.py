"""Tests for life-mode persistent memory primitives."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.memory import (
    Backlog,
    BacklogItem,
    IdentityCard,
    Journal,
    JournalEntry,
    LifeMemory,
)

# ---------- Journal --------------------------------------------------------

def test_journal_append_and_read(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    e1 = JournalEntry.new(kind="mission_complete", title="A", summary="did a", cost_usd=0.10)
    e2 = JournalEntry.new(kind="mission_failed", title="B", summary="did b", cost_usd=0.05)
    j.append(e1)
    j.append(e2)

    rows = j.all()
    assert [r.title for r in rows] == ["A", "B"]
    assert rows[0].kind == "mission_complete"
    assert pytest.approx(j.total_cost_since(0.0)) == 0.15


def test_journal_tail(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    for i in range(10):
        j.append(JournalEntry.new(kind="x", title=f"t{i}", summary="..."))
    tail = j.tail(3)
    assert [e.title for e in tail] == ["t7", "t8", "t9"]
    assert j.tail(0) == []


def test_journal_tail_preserves_rotated_history_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Journal, "ROTATE_BYTES", 256)
    j = Journal(tmp_path / "journal.jsonl")
    j.append(
        JournalEntry.new(
            kind="x",
            title="old",
            summary="o" * 300,
            cost_usd=2.0,
        )
    )
    j.append(JournalEntry.new(kind="x", title="mid", summary="m", cost_usd=3.0))
    j.append(JournalEntry.new(kind="x", title="new", summary="n", cost_usd=5.0))

    assert (tmp_path / "journal.jsonl.1").exists()
    assert [e.title for e in j.tail(3)] == ["old", "mid", "new"]


def test_journal_tail_does_not_call_read_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    for i in range(250):
        j.append(JournalEntry.new(kind="x", title=f"t{i}", summary="..."))

    def _boom(path: Path):  # noqa: ARG001
        raise AssertionError("tail must not call _read_jsonl")

    monkeypatch.setattr("argus_skill.life.memory._read_jsonl", _boom)

    tail = j.tail(3)
    assert [e.title for e in tail] == ["t247", "t248", "t249"]


def test_journal_tolerates_partial_trailing_line(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    j = Journal(p)
    j.append(JournalEntry.new(kind="x", title="ok", summary="..."))
    # Simulate a crash mid-write: partial JSON line.
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "broken"\n')
    rows = j.all()
    assert len(rows) == 1
    assert rows[0].title == "ok"


# ---------- Backlog --------------------------------------------------------

def test_backlog_add_pending_order(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    low = b.add(BacklogItem.new(title="low", objective="...", priority=200))
    hi = b.add(BacklogItem.new(title="hi", objective="...", priority=10))
    mid = b.add(BacklogItem.new(title="mid", objective="...", priority=100))

    pending = b.pending()
    assert [it.title for it in pending] == ["hi", "mid", "low"]
    head = b.next_pending()
    assert head is not None
    assert head.id == hi.id
    # Untouched ids:
    assert {it.id for it in pending} == {low.id, hi.id, mid.id}


def test_backlog_status_transitions(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    item = b.add(BacklogItem.new(title="t", objective="..."))
    head = b.next_pending()
    assert head is not None
    assert head.id == item.id

    b.mark_running(item.id)
    assert b.next_pending() is None  # running ≠ pending
    again = b.all()[0]
    assert again.status == "running"
    assert again.started_ts is not None

    b.mark_done(item.id)
    final = b.all()[0]
    assert final.status == "done"
    assert final.finished_ts is not None
    assert b.next_pending() is None


def test_backlog_failed_carries_error(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    item = b.add(BacklogItem.new(title="t", objective="..."))
    b.mark_failed(item.id, error="boom")
    row = b.all()[0]
    assert row.status == "failed"
    assert row.last_error == "boom"


def test_backlog_unknown_status_normalised(tmp_path: Path) -> None:
    p = tmp_path / "backlog.jsonl"
    p.write_text(
        json.dumps(
            {
                "id": "x", "ts": 0.0, "title": "t", "objective": "o",
                "status": "garbage", "priority": 1, "max_cost_usd": 1.0,
            }
        ) + "\n"
    )
    b = Backlog(p)
    items = b.all()
    assert items[0].status == "pending"


def test_backlog_remove(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "backlog.jsonl")
    a = b.add(BacklogItem.new(title="a", objective="..."))
    bb = b.add(BacklogItem.new(title="b", objective="..."))
    assert b.remove(a.id) is True
    assert [it.title for it in b.all()] == ["b"]
    assert b.remove("nope") is False
    _ = bb  # silence


# ---------- IdentityCard ---------------------------------------------------

def test_identity_default_is_idempotent(tmp_path: Path) -> None:
    card = IdentityCard(tmp_path / "identity.md")
    assert card.read() == ""
    assert card.ensure_default() is True
    body1 = card.read()
    assert "argus-skill" in body1
    # Idempotent — second call returns False, doesn't overwrite.
    assert card.ensure_default() is False
    assert card.read() == body1


def test_identity_user_edit_preserved(tmp_path: Path) -> None:
    p = tmp_path / "identity.md"
    p.write_text("# my own card\n\nVoice: terse.\n", encoding="utf-8")
    card = IdentityCard(p)
    assert card.ensure_default() is False
    assert "my own card" in card.read()


# ---------- LifeMemory facade + retrieval ----------------------------------

def test_life_memory_init(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    state = mem.init()
    assert state == {"identity": True, "journal": True, "backlog": True}
    # Second init should be no-op.
    state2 = mem.init()
    assert state2 == {"identity": False, "journal": False, "backlog": False}
    assert (tmp_path / "identity.md").exists()
    assert (tmp_path / "journal.jsonl").exists()
    assert (tmp_path / "backlog.jsonl").exists()


def test_relevant_journal_keyword_overlap(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="Refactored authentication module",
            summary="Migrated bcrypt usage and tightened JWT validation.",
            tags=["auth", "security"],
        )
    )
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="CSS tweaks",
            summary="Adjusted padding on the homepage hero.",
            tags=["frontend"],
        )
    )
    mem.journal.append(
        JournalEntry.new(
            kind="mission_failed",
            title="Authentication retry attempt",
            summary="Could not lock down the JWT refresh path; left a TODO.",
            tags=["auth"],
        )
    )
    hits = mem.relevant_journal_for(
        "Add a session expiry check to the authentication flow",
        max_entries=2,
    )
    assert len(hits) == 2
    titles = {h.title for h in hits}
    # Both auth-tagged entries should be selected; CSS one excluded.
    assert "CSS tweaks" not in titles
    assert any("uthent" in t for t in titles)


def test_relevant_journal_returns_empty_when_no_overlap(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.journal.append(
        JournalEntry.new(kind="x", title="Pancake recipe", summary="Mix flour with milk.")
    )
    hits = mem.relevant_journal_for("Add a database migration to users table")
    assert hits == []


def test_render_prelude_marks_non_authoritative(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="Database migration helper",
            summary="Added migrate_users.py.",
            tags=["database", "migration"],
        )
    )
    block = mem.render_prelude(
        objective="write a database migration for the orders table"
    )
    assert "non-authoritative" in block.lower()
    assert "ignore them" in block.lower()
    assert "Database migration helper" in block
    # Identity card text appears too:
    assert "argus-skill" in block.lower() or "voice" in block.lower()


def test_render_prelude_empty_when_nothing_relevant_and_no_identity(
    tmp_path: Path,
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.root.mkdir(parents=True, exist_ok=True)
    # Don't init — no identity, no journal.
    assert mem.render_prelude(objective="something") == ""
