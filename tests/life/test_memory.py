"""Tests for life-mode persistent memory primitives."""
from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import pytest

from argus_skill.life.memory import (
    Backlog,
    BacklogItem,
    EventJournal,
    IdentityCard,
    JournalEntry,
    LifeMemory,
)


def _append_history_event(mem: LifeMemory, entry: JournalEntry) -> None:
    path = Path(mem.root) / "events.jsonl"
    mission = entry.kind in {"mission_complete", "mission_failed"}
    row = {
        "type": "life.mission.completed" if mission else "user.note",
        "id": entry.id,
        "item_id": entry.id,
        "ts": entry.ts,
        "success": entry.kind == "mission_complete",
        "title": entry.title,
        "summary": entry.summary,
        "text": entry.summary,
        "tags": entry.tags,
        "cost_usd": entry.cost_usd,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_event_journal_cost_includes_every_retained_rollover(tmp_path: Path) -> None:
    now = time.time()
    path = tmp_path / "events.jsonl"
    rows = [
        (path.with_suffix(".jsonl.2"), 1.0),
        (path.with_suffix(".jsonl.3"), 2.0),
        (path.with_suffix(".jsonl.1"), 3.0),
        (path, 4.0),
    ]
    for index, (target, cost) in enumerate(rows):
        target.write_text(
            json.dumps({
                "type": "life.mission.completed",
                "ts": now + index,
                "cost_usd": cost,
                "success": True,
            }) + "\n",
            encoding="utf-8",
        )

    assert EventJournal(path).total_cost_since(now - 1) == pytest.approx(10.0)


def test_event_journal_reads_every_rollover_in_chronological_order(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    for target, title in (
        (path.with_suffix(".jsonl.2"), "oldest"),
        (path.with_suffix(".jsonl.3"), "older"),
        (path.with_suffix(".jsonl.1"), "recent"),
        (path, "live"),
    ):
        target.write_text(
            json.dumps({
                "type": "user.note",
                "ts": time.time(),
                "title": title,
                "text": title,
            }) + "\n",
            encoding="utf-8",
        )

    journal = EventJournal(path)
    assert [entry.title for entry in journal.all()] == [
        "oldest", "older", "recent", "live",
    ]
    assert [entry.title for entry in journal.tail(3)] == [
        "older", "recent", "live",
    ]


def test_event_journal_total_cost_cache_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(EventJournal, "_TOTAL_COST_CACHE_MAX_ENTRIES", 2)
    journal = EventJournal(path)

    for timestamp in range(5):
        assert journal.total_cost_since(float(timestamp)) == 0.0

    assert list(journal._total_cost_cache) == [3.0, 4.0]


def test_event_journal_projects_canonical_lifecycle_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "life.mission.started",
            "ts": 1.0,
            "item_id": "m1",
            "objective": "legacy mission",
        })
        + "\n"
        + json.dumps({
            "type": "life.mission.completed",
            "ts": 2.0,
            "item_id": "m1",
            "success": True,
        })
        + "\n",
        encoding="utf-8",
    )

    entries = EventJournal(path).all()

    assert [entry.kind for entry in entries] == [
        "mission_started",
        "mission_complete",
    ]


def test_event_journal_projects_legacy_team_waiting_as_planner_waiting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "life.team.waiting",
            "ts": 1.0,
            "reason": "await external worker",
        })
        + "\n",
        encoding="utf-8",
    )

    entries = EventJournal(path).all()

    assert len(entries) == 1
    assert entries[0].kind == "planner_waiting"


def test_event_journal_tail_prefilters_non_journal_json_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    rows = [
        json.dumps({"type": "engineer.progress", "ts": i, "text": "command output"})
        for i in range(2_000)
    ]
    rows.append(json.dumps({"type": "user.note", "ts": 2_001, "text": "keep me"}))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    original = json.loads
    calls = 0

    def _counted(value: str, *args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal calls
        calls += 1
        return original(value, *args, **kwargs)

    monkeypatch.setattr("argus_skill.life.memory.json.loads", _counted)
    tail = EventJournal(path).tail(1)

    assert [entry.summary for entry in tail] == ["keep me"]
    assert calls <= 2


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


def test_parallel_claims_require_disjoint_explicit_ownership(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    first = backlog.add(BacklogItem.new(
        title="first",
        objective="first",
        parallel_safe=True,
        owns_paths=["research/routes/a.md"],
    ))
    overlap = backlog.add(BacklogItem.new(
        title="overlap",
        objective="overlap",
        parallel_safe=True,
        owns_paths=["research/routes"],
    ))
    case_alias = backlog.add(BacklogItem.new(
        title="case alias",
        objective="case alias",
        parallel_safe=True,
        owns_paths=["RESEARCH/ROUTES/A.MD"],
    ))
    glob_alias = backlog.add(BacklogItem.new(
        title="glob alias",
        objective="glob alias",
        parallel_safe=True,
        owns_paths=["research/**"],
    ))
    disjoint = backlog.add(BacklogItem.new(
        title="disjoint",
        objective="disjoint",
        parallel_safe=True,
        owns_paths=["research/debates/b.md"],
    ))
    backlog.add(BacklogItem.new(title="serial", objective="serial"))

    assert backlog.claim_next(expected_id=first.id) is not None
    assert backlog.next_pending(parallel_only=True).id == disjoint.id
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=overlap.id,
    ) is None
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=case_alias.id,
    ) is None
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=glob_alias.id,
    ) is None
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=disjoint.id,
    ) is not None


def test_paused_external_work_keeps_paths_owned_without_blocking_disjoint_work(
    tmp_path: Path,
) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    paused = backlog.add(BacklogItem.new(
        title="long benchmark",
        objective="wait for the benchmark",
        owns_paths=["results/control"],
    ))
    overlap = backlog.add(BacklogItem.new(
        title="overlap",
        objective="touch the control",
        parallel_safe=True,
        owns_paths=["results"],
    ))
    disjoint = backlog.add(BacklogItem.new(
        title="kernel",
        objective="build the next kernel",
        parallel_safe=True,
        owns_paths=["kernels"],
    ))
    backlog.update(paused.id, status="paused_external_work")

    # Selection and atomic claim must use the same active-path rule. They used
    # to disagree only for paused external work: preview returned ``overlap``,
    # claim rejected it, and the supervisor emitted claim_lost forever.
    assert backlog.next_pending(respect_running=True).id == disjoint.id
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=overlap.id,
    ) is None
    assert backlog.claim_next(
        parallel_only=True,
        expected_id=disjoint.id,
    ) is not None


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
                "status": "garbage", "priority": 1,
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


def _backlog_add_worker(
    path: str,
    ready: Any,
    start: Any,
    results: Any,
    *,
    title: str,
    objective: str,
    delay: float = 0.0,
) -> None:
    try:
        ready.put(("ready", "add", title))
        start.wait()
        if delay:
            time.sleep(delay)
        item = BacklogItem.new(title=title, objective=objective)
        backlog = Backlog(Path(path))
        out = backlog.add(item)
        results.put(("ok", "add", title, out.id))
    except Exception as exc:  # noqa: BLE001
        results.put(("err", "add", title, type(exc).__name__, str(exc)))


def _backlog_claim_worker(
    path: str,
    ready: Any,
    start: Any,
    results: Any,
    *,
    name: str,
) -> None:
    try:
        ready.put(("ready", "claim", name))
        start.wait()
        backlog = Backlog(Path(path))
        claimed = backlog.claim_next()
        results.put(
            (
                "ok",
                "claim",
                name,
                None if claimed is None else claimed.id,
                None if claimed is None else claimed.status,
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.put(("err", "claim", name, type(exc).__name__, str(exc)))


def test_backlog_add_and_claim_are_process_safe(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path / "backlog.jsonl")
    seed = backlog.add(BacklogItem.new(title="seed", objective="claim me"))

    ctx = mp.get_context("spawn")
    ready = ctx.Queue()
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_backlog_add_worker,
            args=(str(backlog.path), ready, start, results),
            kwargs={"title": "add-a", "objective": "one", "delay": 0.2},
        ),
        ctx.Process(
            target=_backlog_add_worker,
            args=(str(backlog.path), ready, start, results),
            kwargs={"title": "add-b", "objective": "two", "delay": 0.2},
        ),
        ctx.Process(
            target=_backlog_claim_worker,
            args=(str(backlog.path), ready, start, results),
            kwargs={"name": "claim-a"},
        ),
        ctx.Process(
            target=_backlog_claim_worker,
            args=(str(backlog.path), ready, start, results),
            kwargs={"name": "claim-b"},
        ),
    ]

    for proc in processes:
        proc.start()

    ready_messages = [ready.get(timeout=10) for _ in processes]
    assert len(ready_messages) == 4
    start.set()

    for proc in processes:
        proc.join(timeout=10)
        assert proc.exitcode == 0

    outcomes = [results.get(timeout=10) for _ in processes]
    assert not any(item[0] == "err" for item in outcomes)

    add_ids = [item[3] for item in outcomes if item[0] == "ok" and item[1] == "add"]
    claim_ids = [item[3] for item in outcomes if item[0] == "ok" and item[1] == "claim" and item[3] is not None]
    assert len(add_ids) == 2
    # Adds and claims start together; an added item is allowed to become
    # claimable before the second claimant runs.  The concurrency invariant is
    # that no item is handed out twice and no update is lost.
    assert len(claim_ids) == len(set(claim_ids))
    assert 1 <= len(claim_ids) <= 3

    rows = backlog.all()
    assert len(rows) == 3
    claimed = set(claim_ids)
    assert {
        row.id for row in rows if row.status == "running"
    } == claimed
    assert all(
        row.status == ("running" if row.id in claimed else "pending")
        for row in rows
    )
    assert seed.id in {row.id for row in rows}


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
    assert "my own card" in card.prompt_text()


def test_default_identity_is_not_model_context(tmp_path: Path) -> None:
    card = IdentityCard(tmp_path / "identity.md")
    card.ensure_default()
    assert card.prompt_text() == ""


# ---------- LifeMemory facade + retrieval ----------------------------------

def test_life_memory_init(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    state = mem.init()
    assert state == {"identity": True, "events": True, "backlog": True}
    # Second init should be no-op.
    state2 = mem.init()
    assert state2 == {"identity": False, "events": False, "backlog": False}
    assert (tmp_path / "identity.md").exists()
    assert (tmp_path / "events.jsonl").exists()
    assert (tmp_path / "backlog.jsonl").exists()


def test_relevant_journal_returns_most_recent(tmp_path: Path) -> None:
    # Recency-only retrieval: the harness no longer ranks entries by lexical
    # overlap with the objective. It surfaces the most recent entries and
    # lets the agent judge relevance.
    mem = LifeMemory.open(tmp_path)
    mem.init()
    _append_history_event(
        mem,
        JournalEntry.new(
            kind="mission_complete",
            title="Refactored authentication module",
            summary="Migrated bcrypt usage and tightened JWT validation.",
            tags=["auth", "security"],
        )
    )
    _append_history_event(
        mem,
        JournalEntry.new(
            kind="mission_complete",
            title="CSS tweaks",
            summary="Adjusted padding on the homepage hero.",
            tags=["frontend"],
        )
    )
    _append_history_event(
        mem,
        JournalEntry.new(
            kind="mission_failed",
            title="Authentication retry attempt",
            summary="Could not lock down the JWT refresh path; left a TODO.",
            tags=["auth"],
        )
    )
    hits = mem.recent_journal(max_entries=2)
    assert len(hits) == 2
    titles = [h.title for h in hits]
    # Newest first; the two most recent are returned regardless of keywords.
    assert titles == ["Authentication retry attempt", "CSS tweaks"]
    # The oldest entry falls outside the max_entries window.
    assert "Refactored authentication module" not in titles


def test_relevant_journal_recency_ignores_overlap(tmp_path: Path) -> None:
    # An entry with no lexical overlap is still surfaced — relevance is the
    # agent's call, not the harness's.
    mem = LifeMemory.open(tmp_path)
    mem.init()
    _append_history_event(
        mem,
        JournalEntry.new(kind="x", title="Pancake recipe", summary="Mix flour with milk."),
    )
    hits = mem.recent_journal()
    assert [h.title for h in hits] == ["Pancake recipe"]


def test_render_prelude_marks_non_authoritative(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    _append_history_event(
        mem,
        JournalEntry.new(
            kind="mission_complete",
            title="Database migration helper",
            summary="Added migrate_users.py.",
            tags=["database", "migration"],
        )
    )
    block = mem.render_prelude(max_journal_entries=1)
    assert "non-authoritative" in block.lower()
    assert "ignore it" in block.lower()
    assert "Database migration helper" in block
    assert "operator identity card" not in block.lower()


def test_render_prelude_never_reinjects_planner_error_verdict_body(
    tmp_path: Path,
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    foreign_reason = (
        "Inspected another project's private objective, paths, and reviewer handoff."
    )
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "life.planner.error",
            "ts": time.time(),
            "error": "discarded stale planner verdict outbox after semantic state change",
            "reason": foreign_reason,
        }) + "\n")

    entry = mem.recent_journal(max_entries=1)[0]
    rendered = mem.render_prelude(max_journal_entries=1)

    assert entry.summary == (
        "discarded stale planner verdict outbox after semantic state change"
    )
    assert "reason" not in entry.extra
    assert foreign_reason not in rendered
    assert "discarded stale planner verdict" in rendered


def test_render_prelude_empty_when_nothing_relevant_and_no_identity(
    tmp_path: Path,
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.root.mkdir(parents=True, exist_ok=True)
    # Don't init — no identity, no journal.
    assert mem.render_prelude() == ""
