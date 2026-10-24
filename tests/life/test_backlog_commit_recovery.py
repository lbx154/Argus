"""Backlog commit authority across process loss and partial archive writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.life import memory
from argus.life.memory import Backlog, BacklogItem, IllegalStateTransition


def _running(backlog: Backlog) -> BacklogItem:
    item = backlog.add(BacklogItem.new(title="Storage probe", objective="Complete the local probe"))
    assert backlog.claim_next().id == item.id
    return item


def _interrupt_live_rewrite(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    rewrite = memory._atomic_rewrite_jsonl

    def fail_live(target, rows):
        if target == path:
            raise OSError("interrupted before live replacement")
        rewrite(target, rows)

    monkeypatch.setattr(memory, "_atomic_rewrite_jsonl", fail_live)


@pytest.mark.parametrize("entry", [
    "active", "history", "all", "pending", "ready", "next_pending", "claim_next", "reap_orphans",
])
def test_every_read_and_claim_recovers_committed_completion(tmp_path, monkeypatch, entry):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _running(backlog)
    with monkeypatch.context() as fault:
        _interrupt_live_rewrite(fault, backlog.path)
        with pytest.raises(OSError, match="interrupted"):
            backlog.mark_done(item.id)

    # The old failure window is present on disk before opening a fresh reader.
    assert json.loads(backlog.path.read_text())["status"] == "running"
    assert json.loads(backlog.archive_path.read_text())["status"] == "done"
    reopened = Backlog(backlog.path)
    result = getattr(reopened, entry)()
    if entry in {"history", "all"}:
        assert [(row.id, row.status) for row in result] == [(item.id, "done")]
    elif entry in {"next_pending", "claim_next"}:
        assert result is None
    else:
        assert result == []
    assert reopened.reap_orphans() == []
    assert reopened.claim_next() is None
    assert len(backlog.archive_path.read_text().splitlines()) == 1
    with pytest.raises(IllegalStateTransition):
        reopened.update(item.id, status="pending")
    with pytest.raises(ValueError, match="already exists"):
        reopened.add(BacklogItem.new(item_id=item.id, title="Reuse", objective="Must not revive"))


def test_legacy_live_terminal_duplicate_wins_without_archive(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = BacklogItem.new(title="Legacy", objective="Previously completed")
    item.status = "done"
    terminal = item.to_jsonable()
    item.status = "running"
    backlog.path.write_text(
        json.dumps(terminal) + "\n" + json.dumps(item.to_jsonable()) + "\n"
    )
    assert backlog.reap_orphans() == []
    assert backlog.claim_next() is None
    assert [(row.id, row.status) for row in backlog.history()] == [(item.id, "done")]


@pytest.mark.parametrize("mutation", ["duplicate_id", "wrong_status", "missing_objective", "negative_offset"])
def test_invalid_pending_commit_rows_fail_closed(tmp_path, monkeypatch, mutation):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _running(backlog)
    with monkeypatch.context() as fault:
        _interrupt_live_rewrite(fault, backlog.path)
        with pytest.raises(OSError):
            backlog.mark_done(item.id)
    record = json.loads(backlog._commit_path.read_text())
    if mutation == "duplicate_id":
        record["terminal"].append(dict(record["terminal"][0]))
    elif mutation == "wrong_status":
        record["terminal"][0]["status"] = "running"
    elif mutation == "missing_objective":
        del record["terminal"][0]["objective"]
    else:
        record["archive_offset"] = -1
    backlog._commit_path.write_text(json.dumps(record))
    before_live = backlog.path.read_bytes()
    before_archive = backlog.archive_path.read_bytes()
    with pytest.raises(RuntimeError, match="invalid backlog commit record"):
        Backlog(backlog.path).claim_next()
    assert backlog.path.read_bytes() == before_live
    assert backlog.archive_path.read_bytes() == before_archive


@pytest.mark.parametrize("invalid", [
    "", "{", "[]", "{}", '{"version":true}', '{"version":2}',
    '{"version":1,"archive_offset":0}',
    '{"version":1,"archive_offset":0,"live":[],"terminal":[]}',
    '{"version":1,"archive_offset":0,"live":{},"terminal":[{}]}',
])
def test_invalid_commit_record_fails_closed(tmp_path, invalid):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    _running(backlog)
    before = backlog.path.read_bytes()
    backlog._commit_path.write_text(invalid)
    for entry in ("active", "history", "claim_next", "reap_orphans"):
        with pytest.raises(RuntimeError, match="invalid backlog commit record"):
            getattr(Backlog(backlog.path), entry)()
        assert backlog.path.read_bytes() == before
        assert backlog._commit_path.read_text() == invalid
        assert not backlog.archive_path.exists()


def test_missing_committed_archive_prefix_fails_closed(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    first = _running(backlog)
    backlog.mark_done(first.id)
    second = _running(backlog)
    with monkeypatch.context() as fault:
        _interrupt_live_rewrite(fault, backlog.path)
        with pytest.raises(OSError):
            backlog.mark_done(second.id)
    backlog.archive_path.unlink()
    before = backlog.path.read_bytes()
    with pytest.raises(RuntimeError, match="shorter than its committed offset"):
        Backlog(backlog.path).claim_next()
    assert backlog.path.read_bytes() == before
    assert not backlog.archive_path.exists()
