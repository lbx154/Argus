"""Terminal views read the same committed backlog state as the scheduler."""

from __future__ import annotations

import json

import pytest

from argus.apps._watch import _read_backlog_rows as watch_rows
from argus.apps.cli._follow import _read_backlog_rows as follow_rows
from argus.life.memory import Backlog, BacklogItem


@pytest.mark.parametrize("read_rows", [watch_rows, follow_rows], ids=["watch", "follow"])
def test_terminal_view_finishes_committed_completion(tmp_path, monkeypatch, read_rows):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = backlog.add(BacklogItem.new(title="View probe", objective="Complete the probe"))
    assert backlog.claim_next().id == item.id
    assert read_rows(backlog.path)[0]["status"] == "running"

    def interrupt_before_archive(self, record):
        raise OSError("committed but not applied")

    with monkeypatch.context() as fault:
        fault.setattr(Backlog, "_apply_commit", interrupt_before_archive)
        with pytest.raises(OSError, match="committed but not applied"):
            backlog.mark_done(item.id)

    rows = read_rows(backlog.path)
    if read_rows is watch_rows:
        assert rows == []  # Watch shows current/queued work.
    else:
        assert [(row["id"], row["status"]) for row in rows] == [(item.id, "done")]
        assert rows[0]["title"] == "View probe"  # Follow annotates completed events too.
    assert backlog.history()[0].status == "done"
    assert backlog.claim_next() is None


@pytest.mark.parametrize("read_rows", [watch_rows, follow_rows], ids=["watch", "follow"])
def test_missing_backlog_view_does_not_create_state(tmp_path, read_rows):
    path = tmp_path / "missing-project" / "backlog.jsonl"
    assert read_rows(path) == []
    assert not path.parent.exists()


def test_follow_retains_legacy_terminal_context_after_migration(tmp_path):
    path = tmp_path / "backlog.jsonl"
    item = BacklogItem.new(title="Old result", objective="Explain the completed event")
    item.status = "done"
    path.write_text(json.dumps(item.to_jsonable()) + "\n", encoding="utf-8")

    for _ in range(2):
        rows = follow_rows(path)
        assert rows[0]["title"] == item.title
        assert rows[0]["objective"] == item.objective
        assert rows[0]["status"] == "done"
    assert watch_rows(path) == []
