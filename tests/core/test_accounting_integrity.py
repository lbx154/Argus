"""Fail-closed accounting after interrupted journal writes (issue #132).

Fixtures are produced by the real ledger writer and then damaged the way an
ENOSPC-interrupted append leaves them: a truncated record prefix immediately
followed by a complete later record on one physical line, or a truncated
final line. No provider transport is involved anywhere in these paths.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from argus.core import cost_control, usage
from argus.core.cost_control import (
    COST_CONTROL_AUDIT_FILE,
    COST_CONTROL_FAILED_DIR,
    FAILED_FINALIZATION_REASON,
    _locked,
    acknowledge_unpriced_call,
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
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
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


def test_truncated_prefix_with_complete_record_fails_admission_closed(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    ledger.append(_record(project, "call-2"))
    damaged, clean = _damage_with_concatenated_record(ledger)

    with pytest.raises(UsageJournalIntegrityError) as raised:
        ledger.records()
    error = raised.value
    assert error.reason_code == "corrupt_accounting_journal"
    assert error.path == ledger.path and error.line_number == 2
    # The complete suffix is recognised for the repair report only.
    assert error.recovered_call_id == "call-2"
    assert "call_id=call-2" in str(error) and f"{ledger.path} line 2" in str(error)

    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is None
    assert reason.startswith("accounting_integrity: corrupt_accounting_journal:")
    assert str(ledger.path) in reason and "line 2" in reason
    assert cost_admission_reason(global_root=tmp_path) == reason
    denied = [row for row in _audit_rows(tmp_path) if row["type"] == "budget.reservation.denied"]
    assert denied and denied[-1]["reason_code"] == "corrupt_accounting_journal"
    # Nothing rewrote or "healed" the journal; the evidence stays byte-identical.
    assert ledger.path.read_bytes() == damaged

    # Appending after a damaged tail would splice a new record onto the
    # corruption. The writer refuses instead, so finalization sees a failure.
    with pytest.raises(UsageJournalIntegrityError):
        ledger.append(_record(project, "call-4"))
    assert ledger.path.read_bytes() == damaged

    # Recovery is possible: once the operator restores the physical line, the
    # journal is clean again and admission resumes without any policy bypass.
    ledger.path.write_bytes(clean)
    assert [record.call_id for record in ledger.records()] == ["call-1", "call-2"]
    reservation, reason = _reserve(tmp_path, project, "call-3")
    assert reservation is not None and reason == ""
    reservation.release(reason="test")


def test_truncated_trailing_line_is_corruption_unless_an_append_is_in_progress(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "p1"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(_record(project, "call-1"))
    clean = ledger.path.read_bytes()
    partial = json.dumps(_record(project, "call-2").to_jsonable()).encode("utf-8")[:64]

    # 1. Partial final line, no live writer: ENOSPC left a truncated record.
    ledger.path.write_bytes(clean + partial)
    with pytest.raises(UsageJournalIntegrityError) as raised:
        ledger.records()
    assert raised.value.line_number == 2 and "truncated final record" in str(raised.value)
    assert raised.value.recovered_call_id is None
    _, reason = _reserve(tmp_path, project, "call-3")
    assert reason.startswith("accounting_integrity: corrupt_accounting_journal:")

    # 2. The same bytes while another writer holds the usage lock are an
    # append in progress: no false block for the concurrent writer.
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
    assert reservation is None and "corrupt_accounting_journal" in reason


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


def test_failed_finalization_leaves_a_durable_unresolved_liability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    # The owner PID is already dead when the next admission runs, so the
    # in-memory/state reservation alone would be pruned to nothing.
    reservation, reason = _reserve(tmp_path, project, "interrupted", pid=999_999_999)
    assert reservation is not None and reason == ""

    _finalize_like_the_backend(reservation, project, monkeypatch)
    markers = list((tmp_path / COST_CONTROL_FAILED_DIR).iterdir())
    assert [path.name for path in markers] == [f"{reservation.reservation_id}.json"]
    marker = json.loads(markers[0].read_text(encoding="utf-8"))
    assert marker["call_id"] == "interrupted" and marker["project_id"] == "p1"
    assert marker["reason"].startswith(FAILED_FINALIZATION_REASON)
    assert "ENOSPC" in marker["finalization_error"] or "No space left" in marker["finalization_error"]
    # No zero-cost usage row or synthetic completion was invented.
    assert not (project / "usage.jsonl").exists()

    denied, reason = _reserve(tmp_path, project, "next")
    assert denied is None
    assert reason.startswith("unresolved provider cost") and "call=interrupted" in reason
    assert FAILED_FINALIZATION_REASON in reason and "usage record was not persisted" in reason
    assert cost_admission_reason(global_root=tmp_path) == reason
    snapshot = cost_control_snapshot(global_root=tmp_path)
    assert snapshot["active_reservations"] == 0
    assert snapshot["blocking_unresolved_calls"] == 1
    assert snapshot["unresolved"][0]["call_id"] == "interrupted"

    # A fresh process reading the same directory is blocked too.
    fresh = subprocess.run(
        [sys.executable, "-c", (
            "import sys; from argus.core.cost_control import reserve_call_budget\n"
            "r, reason = reserve_call_budget(call_id='fresh', project_root=sys.argv[1],"
            " mission_id=None, provider='codex', model='gpt-5.6-sol', run_label='engineer-r1',"
            " global_root=sys.argv[2])\n"
            "print('None' if r is None else 'ADMITTED'); print(reason)"
        ), str(project), str(tmp_path)],
        capture_output=True, text=True, check=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
    )
    assert fresh.stdout.splitlines()[0] == "None"
    assert FAILED_FINALIZATION_REASON in fresh.stdout

    # The state file rolls over at midnight; the marker does not.
    tomorrow = time.time() + 86_400
    denied, reason = _reserve(tmp_path, project, "next-day", now=tomorrow)
    assert denied is None and FAILED_FINALIZATION_REASON in reason

    # Only an explicit operator decision retires the liability.
    acknowledge_unpriced_call(
        global_root=tmp_path, project_id="p1", call_id="interrupted",
        liability_usd=5.0, reason="operator accepts the bounded unknown",
    )
    admitted, reason = _reserve(tmp_path, project, "after-ack")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")
    assert not list((tmp_path / COST_CONTROL_FAILED_DIR).iterdir())
    assert cost_control_snapshot(global_root=tmp_path)["pending_liability_usd"] == 5.0


def test_unknown_settlement_behind_a_busy_lock_is_retained_not_dropped(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, reason = _reserve(tmp_path, project, "busy")
    assert reservation is not None and reason == ""
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _locked(tmp_path):
            entered.set()
            release.wait(timeout=2)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=1)
    try:
        assert reservation.settle_unknown(reason="provider interrupted") is True
        assert holder.is_alive()
    finally:
        release.set()
        holder.join(timeout=1)

    assert len(list((tmp_path / COST_CONTROL_FAILED_DIR).iterdir())) == 1
    denied, reason = _reserve(tmp_path, project, "next")
    assert denied is None and FAILED_FINALIZATION_REASON in reason and "provider interrupted" in reason


def test_settled_usage_retires_a_failed_finalization_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    reservation, _ = _reserve(tmp_path, project, "late-receipt")
    assert reservation is not None
    with monkeypatch.context() as patch:
        patch.setattr(cost_control, "_write_state", _enospc)
        with pytest.raises(OSError):
            reservation.settle_unknown(reason="usage record was not persisted")
    assert len(list((tmp_path / COST_CONTROL_FAILED_DIR).iterdir())) == 1

    # A priced usage row for the same call later reaches the ledger.
    UsageLedger(project, migrate_legacy=False).append(_record(project, "late-receipt"))
    admitted, reason = _reserve(tmp_path, project, "next")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")
    assert not list((tmp_path / COST_CONTROL_FAILED_DIR).iterdir())
