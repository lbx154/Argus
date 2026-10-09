"""Fail-closed accounting after interrupted journal writes (issue #132).

Fixtures are produced by the real ledger writer and then damaged the way an
ENOSPC-interrupted append leaves them: a truncated record prefix immediately
followed by a complete later record on one physical line, or a truncated
final line. No provider transport is involved anywhere in these paths.
"""
from __future__ import annotations

import errno
import json
import time
from pathlib import Path

import pytest

from argus.core import cost_control, usage
from argus.core.cost_control import (
    COST_CONTROL_AUDIT_FILE,
    cost_admission_reason,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.token_usage import TokenUsage
from argus.core.usage import UsageJournalIntegrityError, UsageLedger, build_usage_record


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "1000")
    with usage._CALL_ID_CACHE_LOCK:
        usage._CALL_ID_CACHE.clear()


def _record(project: Path, call_id: str, *, model: str = "gpt-5.6-sol"):
    return build_usage_record(
        call_id=call_id,
        project_root=project,
        mission_id="mission-1",
        provider="codex",
        model=model,
        run_label="engineer-r1",
        started_at=time.time() - 1,
        completed_at=time.time(),
        status="completed",
        token_usage=TokenUsage(
            input_tokens=1_000, output_tokens=100,
            input_tokens_present=True, output_tokens_present=True, source="test",
        ),
    )


def _reserve(root: Path, project: Path, call_id: str, **kwargs):
    return reserve_call_budget(
        call_id=call_id, project_root=project, mission_id="mission-1",
        provider="codex", model="gpt-5.6-sol", run_label="engineer-r1",
        global_root=root, **kwargs,
    )


def _damage_with_concatenated_record(ledger: UsageLedger) -> tuple[bytes, bytes]:
    """Write ``call-1`` + ``<truncated call-2 prefix><call-2>``; return (damaged, clean)."""
    clean = ledger.path.read_bytes()
    lines = clean.splitlines(keepends=True)
    assert len(lines) == 2 and all(line.endswith(b"\n") for line in lines)
    truncated_prefix = lines[1][: len(lines[1]) // 2]
    damaged = lines[0] + truncated_prefix + lines[1]
    ledger.path.write_bytes(damaged)
    return damaged, clean


def _audit_rows(root: Path) -> list[dict]:
    path = root / COST_CONTROL_AUDIT_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_admission_repairs_a_damaged_journal_and_keeps_the_evidence(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    damaged, clean = _damage_with_concatenated_record(ledger)

    # The reader itself never skips or heals a malformed line.
    with pytest.raises(UsageJournalIntegrityError) as raised:
        ledger.records()
    error = raised.value
    assert error.reason_code == "corrupt_accounting_journal"
    assert error.path == ledger.path and error.line_number == 2
    assert error.recovered_call_id == "call-2"
    with pytest.raises(UsageJournalIntegrityError):
        ledger.append(_record(project, "call-4"))
    assert ledger.path.read_bytes() == damaged

    # Admission repairs the journal: the damaged bytes are kept beside it,
    # every complete record survives, and the call whose prefix was cut off
    # (call-2, whose complete record followed) needs no liability.
    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    copies = sorted(project.glob("usage.jsonl.damaged-*"))
    assert len(copies) == 1 and copies[0].read_bytes() == damaged
    assert ledger.path.read_bytes() == clean
    assert [record.call_id for record in ledger.records()] == ["call-1", "call-2"]
    assert cost_admission_reason(global_root=tmp_path) == ""
    repaired = [row for row in _audit_rows(tmp_path) if row["type"] == "accounting.journal_repaired"]
    assert len(repaired) == 1
    assert repaired[0]["damaged_copy"] == str(copies[0]) and repaired[0]["kept_records"] == 2
    assert repaired[0]["damaged_lines"][0]["recovered_call_id"] == "call-2"
    assert repaired[0]["liabilities"] == []
    # The journal is a journal again.
    assert ledger.append(_record(project, "call-4"))
    assert [record.call_id for record in ledger.records()] == ["call-1", "call-2", "call-4"]


def test_a_truncated_call_is_written_back_as_a_conservative_liability(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    big = build_usage_record(
        call_id="call-2", project_root=project, mission_id="mission-1", provider="codex",
        model="gpt-5.6-sol", run_label="engineer-r1", started_at=time.time() - 1,
        completed_at=time.time(), status="completed",
        token_usage=TokenUsage(
            input_tokens=50_000, output_tokens=5_000,
            input_tokens_present=True, output_tokens_present=True, source="test",
        ),
    )
    ledger.append(big)
    costs = {record.call_id: record.cost_usd for record in ledger.records()}
    assert costs["call-2"] > costs["call-1"] > 0
    clean = ledger.path.read_bytes()
    # ENOSPC cut call-3's record after its identity and provider were written.
    partial = json.dumps(
        _record(project, "call-3").to_jsonable(), separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    partial = partial[: partial.index(b'"reasoning_output_tokens"')]
    ledger.path.write_bytes(clean + partial)

    reservation, reason = _reserve(tmp_path, project, "call-4")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    records = {record.call_id: record for record in ledger.records()}
    assert set(records) == {"call-1", "call-2", "call-3"}
    liability = records["call-3"]
    assert liability.pricing_tier == "journal_repair_estimate"
    assert liability.cost_basis == "estimate" and liability.pricing_status == "priced"
    assert liability.cost_usd == costs["call-2"]
    assert liability.provider == "codex" and liability.model == "gpt-5.6-sol"
    # The cut fell before run_label; what the prefix no longer said stays blank.
    assert liability.mission_id == "mission-1" and liability.run_label == ""
    assert "truncated at usage.jsonl line 3" in liability.error
    repaired = [row for row in _audit_rows(tmp_path) if row["type"] == "accounting.journal_repaired"]
    assert repaired[-1]["liabilities"] == [{"call_id": "call-3", "cost_usd": costs["call-2"], "line": 3}]
    assert sorted(project.glob("usage.jsonl.damaged-*"))[0].read_bytes() == clean + partial


def test_a_truncated_call_without_a_cost_basis_is_counted_not_refused(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    project.mkdir(parents=True)
    now = time.time()
    ledger.path.write_bytes(
        b'{"call_id":"call-1","completed_at":' + f"{now:.3f}".encode()
        + b',"cost_usd":null,"model":"m","provider":"local-llm","started_at":'
        + f"{now - 10:.3f}".encode() + b',"pro'
    )
    reservation, reason = _reserve(tmp_path, project, "call-2")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    liability = ledger.records()[0]
    assert liability.call_id == "call-1" and liability.pricing_status == "unpriced"
    assert liability.cost_usd is None and "cost unknown" in liability.error
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert [row["call_id"] for row in snapshot["unresolved"]] == ["call-1"]


def test_a_repair_that_cannot_write_keeps_admission_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    damaged, _clean = _damage_with_concatenated_record(ledger)
    monkeypatch.setattr(usage, "_rewrite_usage_rows", _enospc)

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is None
    assert reason.startswith("accounting_integrity: corrupt_accounting_journal:")
    assert ledger.path.read_bytes() == damaged
    assert cost_admission_reason(global_root=tmp_path) == reason


def _journal_with_truncated_tail(project: Path, tail: bytes) -> UsageLedger:
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.path.write_bytes(ledger.path.read_bytes() + tail)
    return ledger


def _truncated_codex_call(project: Path, call_id: str, *, provider: str = "codex") -> bytes:
    row = _record(project, call_id).to_jsonable()
    row["provider"] = provider
    whole = json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return whole[: whole.index(b'"reasoning_output_tokens"')]


def test_the_liability_lands_with_the_repair_not_in_a_later_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = _journal_with_truncated_tail(project, _truncated_codex_call(project, "call-2"))
    # Appends fail with a full disk; the debt must still be in the journal.
    monkeypatch.setattr(UsageLedger, "append", _enospc)

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    records = {record.call_id: record for record in ledger.records()}
    assert set(records) == {"call-1", "call-2"}
    assert records["call-2"].pricing_tier == "journal_repair_estimate"


def test_a_repair_whose_evidence_copy_cannot_be_written_leaves_everything_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = _journal_with_truncated_tail(project, _truncated_codex_call(project, "call-2"))
    damaged = ledger.path.read_bytes()
    monkeypatch.setattr(usage.os, "fsync", _enospc)

    with pytest.raises(OSError):
        ledger.repair_journal()
    assert ledger.path.read_bytes() == damaged
    assert list(project.glob("usage.jsonl.damaged-*")) == []


def test_another_providers_prices_never_price_a_truncated_call(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = _journal_with_truncated_tail(
        project, _truncated_codex_call(project, "call-2", provider="local-llm"),
    )

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    held = {record.call_id: record for record in ledger.records()}["call-2"]
    assert held.provider == "local-llm"
    assert held.pricing_status == "unpriced" and held.cost_usd is None
    assert "call-2" in [row["call_id"] for row in cost_control_snapshot(global_root=tmp_path)["unresolved"]]


def test_a_journal_repaired_by_another_reader_is_read_again_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    _damage_with_concatenated_record(ledger)
    original = UsageLedger.repair_journal

    def repaired_elsewhere(self, **kwargs):
        # Another process won the race: by the time this reader holds the
        # lock the journal is already clean and there is nothing to repair.
        original(self, **kwargs)
        return None

    monkeypatch.setattr(UsageLedger, "repair_journal", repaired_elsewhere)
    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")


def test_a_truncated_record_that_lost_its_call_id_is_still_counted(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = _journal_with_truncated_tail(project, b'{"cached_input_tokens":0,"co')

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    held = [record for record in ledger.records() if record.call_id != "call-1"]
    assert len(held) == 1
    assert held[0].call_id.startswith("journal-repair:usage.jsonl.damaged-")
    assert held[0].call_id.endswith(":2")
    assert held[0].pricing_status == "unpriced" and held[0].cost_usd is None
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert held[0].call_id in [row["call_id"] for row in snapshot["unresolved"]]


def test_the_repair_is_audited_under_the_cost_control_root_wherever_the_project_lives(
    tmp_path: Path,
) -> None:
    project = tmp_path / "work" / "p1"
    _journal_with_truncated_tail(project, _truncated_codex_call(project, "call-2"))

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    repaired = [row for row in _audit_rows(tmp_path) if row["type"] == "accounting.journal_repaired"]
    assert len(repaired) == 1 and repaired[0]["project_id"] == "p1"
    assert repaired[0]["liabilities"][0]["call_id"] == "call-2"
    assert repaired[0]["liabilities"][0]["line"] == 2


def test_truncated_trailing_line_is_corruption_unless_an_append_is_in_progress(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    clean = ledger.path.read_bytes()
    whole = json.dumps(_record(project, "call-2").to_jsonable()).encode("utf-8")
    # The cut falls after the provider, before the model.
    partial = whole[: whole.index(b'"model"')]

    # 1. Partial final line, no live writer: ENOSPC left a truncated record.
    ledger.path.write_bytes(clean + partial)
    with pytest.raises(UsageJournalIntegrityError) as raised:
        ledger.records()
    assert raised.value.line_number == 2 and "truncated final record" in str(raised.value)
    assert raised.value.recovered_call_id is None
    # Admission repairs it: the cut-off record is carried as a liability
    # priced at the journal's costliest comparable call.
    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    repaired = {record.call_id: record for record in ledger.records()}
    assert set(repaired) == {"call-1", "call-2"}
    assert repaired["call-2"].pricing_tier == "journal_repair_estimate"
    assert repaired["call-2"].cost_usd == repaired["call-1"].cost_usd

    # 2. The same bytes while another writer holds the usage lock are an
    # append in progress: no false block for the concurrent writer.
    ledger.path.write_bytes(clean + partial)
    with ledger._locked():
        assert [record.call_id for record in ledger.records()] == ["call-1"]
        # The writer itself holds the lock and must not append onto the
        # partial tail; the locked call-id read that guards ``append`` sees
        # a truncated record.
        with pytest.raises(UsageJournalIntegrityError):
            ledger._call_ids_unlocked()

    # 3. A partial line followed by another record is always corruption,
    # even while a writer is active.
    complete = json.dumps(_record(project, "call-2").to_jsonable()).encode("utf-8") + b"\n"
    ledger.path.write_bytes(clean + partial + complete)
    with ledger._locked():
        with pytest.raises(UsageJournalIntegrityError) as raised:
            ledger.records()
    assert raised.value.line_number == 2 and raised.value.recovered_call_id == "call-2"
    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")
    assert [record.call_id for record in ledger.records()] == ["call-1", "call-2"]
    assert len(list(project.glob("usage.jsonl.damaged-*"))) == 2


def test_reconciliation_refuses_to_rewrite_a_damaged_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.core.pricing import MODEL_PRICES_USD_PER_MTOK

    model = "test-newly-priced-model"
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1", model=model))
    pending = ledger.records()
    assert pending[0].cost_usd is None
    ledger.append(_record(project, "call-2"))
    damaged, _clean = _damage_with_concatenated_record(ledger)

    # The pending row now has a price, so reconciliation would rewrite the file.
    monkeypatch.setitem(MODEL_PRICES_USD_PER_MTOK, model, MODEL_PRICES_USD_PER_MTOK["gpt-5.5"])
    with pytest.raises(UsageJournalIntegrityError):
        ledger._reconcile_token_pricing(pending)
    assert ledger.path.read_bytes() == damaged
    with pytest.raises(UsageJournalIntegrityError):
        ledger.records()
    assert ledger.path.read_bytes() == damaged
    assert not [path for path in project.iterdir() if path.name.startswith(".usage.jsonl.")]


def _enospc(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def _finalize_like_the_backend(reservation, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror the finalizer: usage persistence fails, then settlement fails."""
    with monkeypatch.context() as patch:
        patch.setattr(UsageLedger, "append", _enospc)
        patch.setattr(cost_control, "_write_state", _enospc)
        try:
            UsageLedger(project, migrate_legacy=False).append(_record(project, reservation.call_id))
        except OSError:
            with pytest.raises(OSError):
                reservation.settle_unknown(reason="usage record was not persisted")
