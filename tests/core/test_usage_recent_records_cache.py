"""Today's records come from a per-journal snapshot, not from re-reading the journal.

Budget admission reads every project's records of the day before each provider
call and again every few seconds while the call runs. The snapshot keeps that to
one ``stat`` per journal while nothing changed and to the appended bytes after an
append, without changing what a read returns.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.core import usage
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageLedger, UsageRecord, build_usage_record


@pytest.fixture(autouse=True)
def _fresh_cache():
    with usage._RECENT_RECORDS_CACHE_LOCK:
        usage._RECENT_RECORDS_CACHE.clear()
    yield
    with usage._RECENT_RECORDS_CACHE_LOCK:
        usage._RECENT_RECORDS_CACHE.clear()


def _record(project: Path, call_id: str, *, completed_at: float | None = None) -> UsageRecord:
    now = time.time() if completed_at is None else completed_at
    return build_usage_record(
        call_id=call_id, project_root=project, mission_id="m", provider="codex",
        model="gpt-5.5", run_label="engineer", started_at=now - 1, completed_at=now,
        status="completed",
        token_usage=TokenUsage(
            input_tokens=10, output_tokens=5,
            input_tokens_present=True, output_tokens_present=True, source="test",
        ),
    )


def _today() -> float:
    return usage._local_day_floor(time.time())


def _ids(records: list[UsageRecord]) -> list[str]:
    return [record.call_id for record in records]


def _scan_offsets(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Byte offsets each journal read started from, in order."""
    offsets: list[int] = []
    real = usage._scan_usage_rows

    def spy(path, **kwargs):
        offsets.append(int(kwargs.get("offset", 0)))
        return real(path, **kwargs)

    monkeypatch.setattr(usage, "_scan_usage_rows", spy)
    return offsets


def test_an_unchanged_journal_is_parsed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    offsets = _scan_offsets(monkeypatch)

    assert _ids(ledger.records(since=_today())) == ["a"]
    assert _ids(ledger.records(since=_today())) == ["a"]
    assert _ids(UsageLedger(tmp_path, migrate_legacy=False).records(since=_today())) == ["a"]
    assert offsets == [0]


def test_appended_records_are_read_from_where_the_last_read_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    assert _ids(ledger.records(since=_today())) == ["a"]
    consumed = ledger.path.stat().st_size
    ledger.append(_record(tmp_path, "b"))
    offsets = _scan_offsets(monkeypatch)

    assert _ids(ledger.records(since=_today())) == ["a", "b"]
    assert offsets == [consumed]


def test_a_rewritten_journal_is_parsed_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    ledger.append(_record(tmp_path, "b"))
    assert _ids(ledger.records(since=_today())) == ["a", "b"]
    rows = [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").splitlines()]
    usage._rewrite_usage_rows(ledger.path, rows[:1])
    offsets = _scan_offsets(monkeypatch)

    assert _ids(ledger.records(since=_today())) == ["a"]
    assert offsets == [0]


def test_a_partial_final_line_is_read_once_its_writer_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    assert _ids(ledger.records(since=_today())) == ["a"]
    line = json.dumps(_record(tmp_path, "b").to_jsonable(), sort_keys=True, separators=(",", ":"))
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(line[:40])
    monkeypatch.setattr(usage, "_usage_writer_active", lambda _lock_path: True)

    assert _ids(ledger.records(since=_today())) == ["a"]

    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(line[40:] + "\n")
    monkeypatch.setattr(usage, "_usage_writer_active", lambda _lock_path: False)

    assert _ids(ledger.records(since=_today())) == ["a", "b"]


def test_corruption_still_fails_closed_and_leaves_nothing_cached(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"call_id":"cut","provider":"codex"{"call_id":"c","completed_at":1}\n')

    with pytest.raises(usage.UsageJournalIntegrityError):
        ledger.records(since=_today())
    assert str(ledger.path.resolve()) not in usage._RECENT_RECORDS_CACHE


def test_reads_reaching_before_today_still_parse_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    yesterday = _today() - 3600
    ledger.append(_record(tmp_path, "old", completed_at=yesterday))
    ledger.append(_record(tmp_path, "new"))
    offsets = _scan_offsets(monkeypatch)

    assert _ids(ledger.records(since=_today())) == ["new"]
    assert _ids(ledger.records(since=yesterday - 1)) == ["old", "new"]
    assert _ids(ledger.records()) == ["old", "new"]
    assert _ids(ledger.records(since=_today(), mission_id="m")) == ["new"]
    assert offsets == [0, 0, 0]


def test_a_duplicate_call_id_later_in_the_journal_is_still_ignored(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    yesterday = _today() - 3600
    ledger.append(_record(tmp_path, "dup", completed_at=yesterday))
    stale = _record(tmp_path, "dup").to_jsonable()
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stale, sort_keys=True, separators=(",", ":")) + "\n")

    assert ledger.records(since=_today()) == []
    assert _ids(ledger.records()) == ["dup"]
    assert ledger.records()[0].completed_at == pytest.approx(yesterday)


def test_the_snapshot_follows_the_day_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day_one = _today() - 10 * 86_400
    monkeypatch.setattr(usage, "_local_day_floor", lambda _now: day_one)
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "early", completed_at=day_one + 10))
    ledger.append(_record(tmp_path, "late", completed_at=day_one + 100))
    assert _ids(ledger.records(since=day_one + 50)) == ["late"]

    day_two = day_one + 86_400
    monkeypatch.setattr(usage, "_local_day_floor", lambda _now: day_two)
    ledger.append(_record(tmp_path, "next", completed_at=day_two + 5))

    assert _ids(ledger.records(since=day_two)) == ["next"]
    snapshot = usage._RECENT_RECORDS_CACHE[str(ledger.path.resolve())]
    assert snapshot.floor == day_two
    assert _ids(list(snapshot.records)) == ["next"]
    assert _ids(ledger.records(since=day_one)) == ["early", "late", "next"]


def test_the_snapshot_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "_RECENT_RECORDS_CACHE_MAX_PROJECTS", 2)
    ledgers = [UsageLedger(tmp_path / f"p{index}", migrate_legacy=False) for index in range(3)]
    for index, ledger in enumerate(ledgers):
        ledger.append(_record(ledger.project_root, f"c{index}"))
        assert _ids(ledger.records(since=_today())) == [f"c{index}"]

    assert len(usage._RECENT_RECORDS_CACHE) == 2
    assert str(ledgers[0].path.resolve()) not in usage._RECENT_RECORDS_CACHE


def test_repairing_the_journal_drops_its_snapshot(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    assert _ids(ledger.records(since=_today())) == ["a"]
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"call_id":"cut"\n')

    assert ledger.repair_journal() is not None
    assert str(ledger.path.resolve()) not in usage._RECENT_RECORDS_CACHE
    assert _ids(ledger.records(since=_today())) == ["a"]


def test_a_journal_rewritten_in_place_is_parsed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger(tmp_path, migrate_legacy=False)
    ledger.append(_record(tmp_path, "a"))
    assert _ids(ledger.records(since=_today())) == ["a"]
    # Same inode, longer content: an editor or a test rewrote the journal in place.
    replacement = _record(tmp_path, "b").to_jsonable()
    ledger.path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    offsets = _scan_offsets(monkeypatch)

    assert _ids(ledger.records(since=_today())) == ["b"]
    assert offsets == [0]
