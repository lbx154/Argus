"""The knowledge journal records what was learned, recalled and shared, and never fails a caller."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from argus.wiki import journal
from argus.wiki.journal import (
    append_knowledge_event,
    iter_knowledge_events,
    journal_path,
    read_knowledge_events,
    reuse_counts,
)


def _lines(root: Path) -> list[dict]:
    return [json.loads(line) for line in journal_path(root).read_text(encoding="utf-8").splitlines()]


def test_read_returns_newest_first_with_limit_and_kind_filter(tmp_path):
    for index in range(6):
        append_knowledge_event(
            tmp_path,
            kind=("learned", "recalled", "promoted")[index % 3],
            scope="global",
            path=f"pages/p{index}.md",
            ts=1_000 + index,
        )
    assert [row["path"] for row in read_knowledge_events(tmp_path)] == [
        "pages/p5.md", "pages/p4.md", "pages/p3.md", "pages/p2.md", "pages/p1.md", "pages/p0.md",
    ]
    assert [row["path"] for row in read_knowledge_events(tmp_path, limit=2)] == ["pages/p5.md", "pages/p4.md"]
    assert [row["path"] for row in read_knowledge_events(tmp_path, kinds=["recalled"])] == [
        "pages/p4.md", "pages/p1.md",
    ]
    assert [row["path"] for row in read_knowledge_events(tmp_path, kinds="learned", limit=1)] == ["pages/p3.md"]
    assert read_knowledge_events(tmp_path, kinds=["nothing"]) == []
    assert read_knowledge_events(tmp_path, limit=0) == []


def test_reuse_counts_count_only_recalled_records_per_page(tmp_path):
    lesson = dict(scope="vertical", vertical="research", path="pages/lessons/a.md")
    append_knowledge_event(tmp_path, kind="learned", **lesson)
    append_knowledge_event(tmp_path, kind="recalled", role="engineer", **lesson)
    append_knowledge_event(tmp_path, kind="recalled", role="reviewer", **lesson)
    append_knowledge_event(tmp_path, kind="recalled", scope="global", path="pages/hosts.md")
    append_knowledge_event(tmp_path, kind="promoted", scope="global", path="pages/hosts.md")
    append_knowledge_event(tmp_path, kind="recalled", scope="project", path="")
    assert reuse_counts(tmp_path) == {
        ("vertical", "research", "pages/lessons/a.md"): 2,
        ("global", "", "pages/hosts.md"): 1,
    }


def test_unknown_kind_is_dropped_with_a_log_line_not_an_error(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="argus.wiki.journal"):
        append_knowledge_event(tmp_path, kind="forgotten", path="pages/x.md")
    assert not journal_path(tmp_path).exists()
    assert "unknown kind" in caplog.text


def test_torn_and_garbage_lines_are_skipped_on_read(tmp_path):
    append_knowledge_event(tmp_path, kind="learned", path="pages/first.md", ts=1)
    with journal_path(tmp_path).open("ab") as handle:
        handle.write(b"not json at all\n")
        handle.write(b'{"kind": 3, "path": "pages/not-a-record.md"}\n')
        handle.write(b'["a", "list"]\n')
    append_knowledge_event(tmp_path, kind="recalled", path="pages/second.md", ts=2)
    with journal_path(tmp_path).open("ab") as handle:
        handle.write(b'{"kind": "learned", "path": "pages/torn')
    assert [row["path"] for row in read_knowledge_events(tmp_path)] == ["pages/second.md", "pages/first.md"]
    assert [row["path"] for row in iter_knowledge_events(tmp_path)] == ["pages/first.md", "pages/second.md"]
    assert reuse_counts(tmp_path) == {("", "", "pages/second.md"): 1}


def test_tail_read_stops_after_its_byte_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "_TAIL_CHUNK_BYTES", 64)
    for index in range(50):
        append_knowledge_event(tmp_path, kind="learned", path=f"pages/{index:04d}.md", ts=index)
    monkeypatch.setattr(journal, "_MAX_TAIL_SCAN_BYTES", 256)
    rows = read_knowledge_events(tmp_path, limit=1_000)
    assert 0 < len(rows) < 50
    assert rows[0]["path"] == "pages/0049.md"


def test_long_text_fields_are_bounded(tmp_path):
    append_knowledge_event(tmp_path, kind="learned", path="pages/x.md", note="n" * 10_000)
    (row,) = _lines(tmp_path)
    assert len(row["note"]) == journal._TEXT_LIMIT
