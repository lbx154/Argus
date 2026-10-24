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


def test_the_snapshot_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "_RECENT_RECORDS_CACHE_MAX_PROJECTS", 2)
    ledgers = [UsageLedger(tmp_path / f"p{index}", migrate_legacy=False) for index in range(3)]
    for index, ledger in enumerate(ledgers):
        ledger.append(_record(ledger.project_root, f"c{index}"))
        assert _ids(ledger.records(since=_today())) == [f"c{index}"]

    assert len(usage._RECENT_RECORDS_CACHE) == 2
    assert str(ledgers[0].path.resolve()) not in usage._RECENT_RECORDS_CACHE
