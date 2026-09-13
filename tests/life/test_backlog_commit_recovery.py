"""Backlog commit authority across process loss and partial archive writes."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path

import pytest

from argus_skill.life import memory
from argus_skill.life.memory import Backlog, BacklogItem, IllegalStateTransition


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


def _crash_writer(path: str, item_id: str, boundary: str) -> None:
    """Spawned process: terminate without running Python context cleanup."""
    backlog = Backlog(Path(path))
    rewrite = memory._atomic_rewrite_jsonl
    apply_commit = Backlog._apply_commit

    def crash_rewrite(target, rows):
        rows = list(rows)
        if boundary == "archive_written" and target == backlog.path:
            os._exit(23)
        if boundary == "live_written" and target == backlog._commit_path and rows == [{"version": 1}]:
            os._exit(23)
        rewrite(target, rows)

    def crash_apply(self, record):
        if boundary == "commit_written":
            os._exit(23)
        if boundary == "partial_archive":
            with self.archive_path.open("ab") as handle:
                handle.write(b'{"id":"partial')
                handle.flush()
                os.fsync(handle.fileno())
            os._exit(23)
        apply_commit(self, record)

    memory._atomic_rewrite_jsonl = crash_rewrite
    Backlog._apply_commit = crash_apply
    backlog.mark_done(item_id)


@pytest.mark.parametrize("boundary", [
    "commit_written", "partial_archive", "archive_written", "live_written",
])
def test_process_death_and_interrupted_recovery_are_idempotent(tmp_path, monkeypatch, boundary):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    historical = _running(backlog)
    backlog.mark_done(historical.id)
    prefix = backlog.archive_path.read_bytes()
    item = _running(backlog)
    dependent = backlog.add(BacklogItem.new(
        title="Follow-up", objective="Use the completed result", deps=[item.id],
    ))
    process = mp.get_context("spawn").Process(
        target=_crash_writer, args=(str(backlog.path), item.id, boundary),
    )
    process.start()
    process.join(timeout=15)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("fault injection child did not terminate")
    assert process.exitcode == 23

    # Fail twice more while replaying: the fixed archive offset must prevent
    # duplicate terminal rows and preserve all history before that offset.
    for _ in range(2):
        with monkeypatch.context() as fault:
            _interrupt_live_rewrite(fault, backlog.path)
            with pytest.raises(OSError, match="interrupted"):
                Backlog(backlog.path).active()
    reopened = Backlog(backlog.path)
    assert reopened.reap_orphans() == []
    assert reopened.claim_next().id == dependent.id
    assert reopened.claim_next() is None
    assert backlog.archive_path.read_bytes().startswith(prefix)
    rows = [json.loads(line) for line in backlog.archive_path.read_text().splitlines()]
    assert [(row["id"], row["status"]) for row in rows] == [
        (historical.id, "done"), (item.id, "done"),
    ]


def test_completion_before_commit_point_is_not_recovered(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _running(backlog)
    rewrite = memory._atomic_rewrite_jsonl

    def fail_commit(target, rows):
        if target == backlog._commit_path:
            raise OSError("commit not written")
        rewrite(target, rows)

    with monkeypatch.context() as fault:
        fault.setattr(memory, "_atomic_rewrite_jsonl", fail_commit)
        with pytest.raises(OSError, match="commit not written"):
            backlog.mark_done(item.id)
    reopened = Backlog(backlog.path)
    assert reopened.active()[0].status == "running"
    assert reopened.reap_orphans()[0].status == "pending"
    assert reopened.claim_next().id == item.id


def test_legacy_terminal_overlap_reconciles_once_without_claim_archive_scan(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    old = BacklogItem.new(title="Old", objective="Previously completed")
    old.status = "running"
    pending = BacklogItem.new(title="Current", objective="Still pending")
    backlog.path.write_text("".join(json.dumps(row.to_jsonable()) + "\n" for row in [old, pending]))
    old.status = "done"
    first_revision = old.to_jsonable()
    old.notes = "latest correction"
    backlog.archive_path.write_text("".join(
        json.dumps(row) + "\n" for row in [first_revision, first_revision, old.to_jsonable()]
    ))
    assert [(row.id, row.status) for row in backlog.history()] == [
        (old.id, "done"), (pending.id, "pending"),
    ]
    assert backlog.history()[0].notes == "latest correction"

    def no_archive_scan(self):
        raise AssertionError("normal flat backlog access must not read archive history")

    monkeypatch.setattr(Backlog, "_load_archive", no_archive_scan)
    # A fresh Backlog instance must use the persisted storage version, rather
    # than repeatedly scanning history on each process restart.
    reopened = Backlog(backlog.path)
    assert [row.id for row in reopened.active()] == [pending.id]
    assert reopened.reap_orphans() == []
    assert reopened.claim_next().id == pending.id
    assert reopened.claim_next() is None


def test_missing_legacy_archive_preserves_running_work(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = BacklogItem.new(title="Legacy", objective="Unfinished work")
    item.status = "running"
    backlog.path.write_text(json.dumps(item.to_jsonable()) + "\n")
    assert backlog.active()[0].status == "running"
    assert backlog.reap_orphans()[0].status == "pending"
    assert backlog.claim_next().id == item.id


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


def test_first_terminal_commit_can_recover_before_archive_exists(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _running(backlog)

    def interrupted(self, record):
        raise OSError("archive not opened")

    with monkeypatch.context() as fault:
        fault.setattr(Backlog, "_apply_commit", interrupted)
        with pytest.raises(OSError, match="archive not opened"):
            backlog.mark_done(item.id)
    assert not backlog.archive_path.exists()
    assert Backlog(backlog.path).active() == []
    assert backlog.history()[0].status == "done"


def test_plan_replacement_recovers_new_live_rows_with_terminal_sources(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    source = backlog.add(BacklogItem.new(
        title="Old plan", objective="Outdated work", plan_id="v1", plan_version=1, node_key="old",
    ))
    replacement = BacklogItem.new(
        title="New plan", objective="Replacement work", plan_id="v2", plan_version=2, node_key="new",
    )
    with monkeypatch.context() as fault:
        _interrupt_live_rewrite(fault, backlog.path)
        with pytest.raises(OSError, match="interrupted"):
            backlog.apply_plan_revision(
                expected_plan_id="v1", expected_version=1,
                new_plan_id="v2", new_version=2,
                supersede_item_ids=[source.id], new_items=[replacement], reason="new evidence",
            )
    reopened = Backlog(backlog.path)
    assert [(row.id, row.status) for row in reopened.history()] == [
        (source.id, "superseded"), (replacement.id, "pending"),
    ]
    assert reopened.claim_next().id == replacement.id
    assert reopened.claim_next() is None


def test_terminal_revision_recovers_without_reviving_or_duplicating(tmp_path, monkeypatch):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = _running(backlog)
    backlog.mark_done(item.id)
    with monkeypatch.context() as fault:
        _interrupt_live_rewrite(fault, backlog.path)
        with pytest.raises(OSError, match="interrupted"):
            backlog.update(item.id, notes="corrected terminal result")
    reopened = Backlog(backlog.path)
    assert reopened.history()[0].notes == "corrected terminal result"
    assert reopened.claim_next() is None
    assert len(backlog.archive_path.read_text().splitlines()) == 2


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
