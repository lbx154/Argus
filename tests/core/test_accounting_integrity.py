"""A damaged usage journal is repaired on read, never silently healed or summed.

Fixtures are produced by the real ledger writer and then damaged the way an
ENOSPC-interrupted append leaves them: a truncated record prefix immediately
followed by a complete later record on one physical line, or a truncated
final line.
"""
from __future__ import annotations

import errno
import json
import time
from pathlib import Path

import pytest

from argus.core import usage
from argus.core.token_usage import TokenUsage
from argus.core.usage import (
    UsageJournalIntegrityError,
    UsageLedger,
    build_usage_record,
    daily_records,
    global_daily_usage_summary,
)


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    with usage._CALL_ID_CACHE_LOCK:
        usage._CALL_ID_CACHE.clear()


def _record(project: Path, call_id: str, *, model: str = "gpt-5.6-sol"):
    return build_usage_record(
        call_id=call_id, project_root=project, mission_id="mission-1", provider="codex",
        model=model, run_label="engineer-r1", started_at=time.time() - 1,
        completed_at=time.time(), status="completed",
        token_usage=TokenUsage(
            input_tokens=1_000, output_tokens=100,
            input_tokens_present=True, output_tokens_present=True, source="test",
        ),
    )


def _damage_with_concatenated_record(ledger: UsageLedger) -> tuple[bytes, bytes]:
    """Write ``call-1`` + ``<truncated call-2 prefix><call-2>``; return (damaged, clean)."""
    clean = ledger.path.read_bytes()
    lines = clean.splitlines(keepends=True)
    assert len(lines) == 2 and all(line.endswith(b"\n") for line in lines)
    damaged = lines[0] + lines[1][: len(lines[1]) // 2] + lines[1]
    ledger.path.write_bytes(damaged)
    return damaged, clean


def _enospc(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def test_a_repair_that_cannot_write_surfaces_the_error_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    damaged, _clean = _damage_with_concatenated_record(ledger)
    monkeypatch.setattr(usage, "_rewrite_usage_rows", _enospc)

    with pytest.raises(OSError):
        daily_records(ledger, 0.0)
    assert ledger.path.read_bytes() == damaged
    # The host-wide total leaves that project out instead of failing.
    assert global_daily_usage_summary(global_root=tmp_path).call_count == 0


def test_a_repair_whose_evidence_copy_cannot_be_written_leaves_everything_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.path.write_bytes(ledger.path.read_bytes() + b'{"call_id":"call-2","pro')
    damaged = ledger.path.read_bytes()
    monkeypatch.setattr(usage.os, "fsync", _enospc)

    with pytest.raises(OSError):
        ledger.repair_journal()
    assert ledger.path.read_bytes() == damaged
    assert list(project.glob("usage.jsonl.damaged-*")) == []


def test_a_journal_repaired_by_another_reader_is_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    _damage_with_concatenated_record(ledger)
    original = UsageLedger.repair_journal

    def repaired_elsewhere(self, **kwargs):
        original(self, **kwargs)  # another process won the race
        return None

    monkeypatch.setattr(UsageLedger, "repair_journal", repaired_elsewhere)
    assert [record.call_id for record in daily_records(ledger, 0.0)] == ["call-1", "call-2"]


def test_truncated_trailing_line_is_corruption_unless_an_append_is_in_progress(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    clean = ledger.path.read_bytes()
    whole = json.dumps(_record(project, "call-2").to_jsonable()).encode("utf-8")
    partial = whole[: whole.index(b'"model"')]

    # 1. Partial final line, no live writer: ENOSPC left a truncated record.
    ledger.path.write_bytes(clean + partial)
    with pytest.raises(UsageJournalIntegrityError) as raised:
        ledger.records()
    assert raised.value.line_number == 2 and "truncated final record" in str(raised.value)
    assert [record.call_id for record in daily_records(ledger, 0.0)] == ["call-1"]

    # 2. The same bytes while another writer holds the usage lock are an
    # append in progress: the reader sees the complete records only.
    ledger.path.write_bytes(clean + partial)
    with ledger._locked():
        assert [record.call_id for record in ledger.records()] == ["call-1"]
        with pytest.raises(UsageJournalIntegrityError):
            ledger._call_ids_unlocked()

    # 3. A partial line followed by another record is always corruption.
    ledger.path.write_bytes(clean + partial + whole + b"\n")
    with ledger._locked():
        with pytest.raises(UsageJournalIntegrityError) as raised:
            ledger.records()
    assert raised.value.line_number == 2 and raised.value.recovered_call_id == "call-2"
    assert [record.call_id for record in daily_records(ledger, 0.0)] == ["call-1", "call-2"]
    assert len(list(project.glob("usage.jsonl.damaged-*"))) == 2
