"""Terminal views read the same committed backlog state as the scheduler."""

from __future__ import annotations

import json

import pytest

from argus.apps._watch import _read_backlog_rows as watch_rows
from argus.apps.cli._follow import _read_backlog_rows as follow_rows
from argus.life.memory import BacklogItem


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
