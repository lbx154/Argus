"""Tests for life-mode persistent memory primitives."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from argus.life.memory import (
    Backlog,
    BacklogItem,
    EventJournal,
    IdentityCard,
    LifeMemory,
)


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


def test_event_journal_projects_legacy_team_waiting_as_planner_idle(
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
    assert entries[0].kind == "planner_idle"


def _append_settlement_event(
    path: Path,
    *,
    item_id: str,
    status: str = "done",
    success: bool = True,
    iteration: dict[str, Any] | None = None,
    title: str = "node",
) -> None:
    row: dict[str, Any] = {
        "type": "life.mission.completed",
        "item_id": item_id,
        "ts": time.time(),
        "success": success,
        "status": status,
        "title": title,
        "summary": status,
    }
    if iteration is not None:
        row["iteration"] = iteration
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_event_journal_tail_settlements_kinds_filter_owns_window_slots(
    tmp_path: Path,
) -> None:
    """With ``kinds``, only matching settlements occupy window slots."""
    path = tmp_path / "events.jsonl"
    _append_settlement_event(
        path, item_id="aaaa1111bbbb", status="no_progress", success=False,
        title="failed",
    )
    # Neutral settlements: a budget pause and an iteration requeue.
    _append_settlement_event(
        path, item_id="bbbb2222cccc", status="paused_budget", success=False,
        title="paused",
    )
    _append_settlement_event(
        path, item_id="cccc3333dddd", status="done", success=True,
        iteration={"requeued": True}, title="requeued",
    )
    _append_settlement_event(
        path, item_id="dddd4444eeee", status="done", success=True, title="ok",
    )

    journal = EventJournal(path)
    filtered = journal.tail_settlements(
        2, kinds={"mission_failed", "mission_complete"}
    )
    unfiltered = journal.tail_settlements(2)

    assert [entry.title for entry in filtered] == ["failed", "ok"]
    assert [entry.kind for entry in filtered] == [
        "mission_failed", "mission_complete",
    ]
    # Without the filter the same window is spent on the neutral noise.
    assert [entry.title for entry in unfiltered] == ["requeued", "ok"]


def test_event_journal_tail_settlements_spans_rollovers_and_truncates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    # A different item per generation: the settlement tail is journal-wide.
    for target, title, item_id in (
        (path.with_suffix(".jsonl.2"), "oldest", "item2222aaaa"),
        (path.with_suffix(".jsonl.3"), "older", "item3333bbbb"),
        (path.with_suffix(".jsonl.1"), "recent", "item1111cccc"),
        (path, "live", "item0000dddd"),
    ):
        _append_settlement_event(
            target, item_id=item_id, status="no_progress", success=False,
            title=title,
        )

    journal = EventJournal(path)
    assert [entry.title for entry in journal.tail_settlements(10)] == [
        "oldest", "older", "recent", "live",
    ]
    assert [entry.title for entry in journal.tail_settlements(2)] == [
        "recent", "live",
    ]
    assert journal.tail_settlements(0) == []


def test_event_journal_tail_kinds_spans_rollovers_and_truncates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    for target, title, item_id in (
        (path.with_suffix(".jsonl.2"), "oldest", "item2222aaaa"),
        (path.with_suffix(".jsonl.3"), "older", "item3333bbbb"),
        (path.with_suffix(".jsonl.1"), "recent", "item1111cccc"),
        (path, "live", "item0000dddd"),
    ):
        _append_settlement_event(
            target, item_id=item_id, status="no_progress", success=False,
            title=title,
        )

    journal = EventJournal(path)
    assert [entry.title for entry in journal.tail_kinds(
        10, kinds={"mission_failed"},
    )] == ["oldest", "older", "recent", "live"]
    assert [entry.title for entry in journal.tail_kinds(
        2, kinds={"mission_failed"},
    )] == ["recent", "live"]
    assert journal.tail_kinds(0, kinds={"mission_failed"}) == []


# ---------- Backlog --------------------------------------------------------


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


# ---------- IdentityCard ---------------------------------------------------


def test_untouched_pre_rename_default_identity_is_still_the_default(tmp_path: Path) -> None:
    """An install seeded before the rename must not start injecting the template as operator text.

    The pre-rename card differs from the current one only in its heading
    (``# argus-skill -- operator identity card``); ``prompt_text()`` treats both
    as "no identity" and ``ensure_default()`` upgrades the byte-identical old
    default to the current template.
    """
    from argus.life.memory import _DEFAULT_IDENTITY, _LEGACY_DEFAULT_IDENTITY

    assert _LEGACY_DEFAULT_IDENTITY.startswith("# argus-skill — operator identity card\n")
    assert _DEFAULT_IDENTITY.startswith("# argus — operator identity card\n")
    assert _LEGACY_DEFAULT_IDENTITY.split("\n", 1)[1] == _DEFAULT_IDENTITY.split("\n", 1)[1]

    path = tmp_path / "identity.md"
    path.write_text(_LEGACY_DEFAULT_IDENTITY, encoding="utf-8")  # the on-disk card of an old install
    card = IdentityCard(path)
    assert card.prompt_text() == ""

    assert card.ensure_default() is True
    assert card.read() == _DEFAULT_IDENTITY
    assert card.prompt_text() == ""
    assert card.ensure_default() is False


# ---------- LifeMemory facade + retrieval ----------------------------------


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


# ---------------------------------------------------------------------------
# GPUs against the backlog
# ---------------------------------------------------------------------------


def _cards(total, busy=()):
    return tuple(index in set(busy) for index in range(total))


def _gpu_backlog(tmp_path, monkeypatch, cards):
    from argus.life import memory as memory_module

    monkeypatch.setattr(memory_module, "_gpu_cards", lambda: cards)
    memory = LifeMemory.open(tmp_path / "life")
    return memory.backlog


def _arm(name, gpu_count):
    return BacklogItem.new(
        title=f"arm {name}", objective="train", gpu_count=gpu_count,
        parallel_safe=True, owns_paths=[f"runs/{name}"],
    )


def test_cards_busy_with_another_process_hold_back_claims(tmp_path, monkeypatch):
    backlog = _gpu_backlog(tmp_path, monkeypatch, _cards(4, busy={0, 1, 2}))
    backlog.add(_arm("a", 2))
    assert backlog.gpu_summary() == {"total": 4, "busy": 3, "reserved": 0, "free": 1}
    assert backlog.claim_next(parallel_only=True) is None


def test_a_running_tasks_cards_count_once_whether_or_not_its_job_started(tmp_path, monkeypatch):
    from argus.life import memory as memory_module

    backlog = _gpu_backlog(tmp_path, monkeypatch, _cards(4))
    first, second, third = (backlog.add(_arm(name, 2)) for name in "abc")
    assert backlog.claim_next(parallel_only=True).id == first.id
    assert backlog.gpu_summary() == {"total": 4, "busy": 0, "reserved": 2, "free": 2}
    # Its Engineer starts the job on two cards of its own choosing, now both
    # busy and reserved.
    monkeypatch.setattr(memory_module, "_gpu_cards", lambda: _cards(4, busy={2, 3}))
    assert backlog.gpu_summary() == {"total": 4, "busy": 2, "reserved": 2, "free": 2}
    assert backlog.claim_next(parallel_only=True).id == second.id
    assert backlog.claim_next(parallel_only=True) is None
    assert next(row for row in backlog.all() if row.id == third.id).status == "pending"


def test_without_a_visible_gpu_nothing_is_gated(tmp_path, monkeypatch):
    backlog = _gpu_backlog(tmp_path, monkeypatch, None)
    item = backlog.add(BacklogItem.new(title="train", objective="train", gpu_count=8))
    assert backlog.gpu_summary() is None
    assert [ready.id for ready in backlog.ready()] == [item.id]


# ---------------------------------------------------------------------------
# CPU cores against the backlog
# ---------------------------------------------------------------------------
