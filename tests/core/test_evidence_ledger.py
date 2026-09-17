from __future__ import annotations

import json

import pytest

from argus.core.evidence_ledger import EvidenceLedger


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_correction_preserves_original_and_binds_its_digest(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    ledger = EvidenceLedger(path)
    original = ledger.append_record(
        record_id="run-1",
        record_type="experiment",
        payload={"run_id": "run-1", "state": "error", "exit_code": 1},
        created_at=10,
    )

    correction = ledger.append_correction(
        correction_id="run-1-teardown-correction",
        target_record_id="run-1",
        relation="reclassifies",
        reason="Training completed before a teardown guard false-positive.",
        evidence_refs=["logs/train.log", "checkpoint/step-37018"],
        payload={"accepted_terminal_state": "completed"},
        created_at=20,
    )

    rows = _rows(path)
    assert rows[0] == original
    assert rows[0]["state"] == "error"
    assert rows[0]["exit_code"] == 1
    assert rows[1] == correction
    assert correction["target_record_sha256"] == original["_ledger"]["payload_sha256"]
    assert correction["_ledger"]["record_type"] == "correction"
    assert ledger.history("run-1") == rows


def test_identical_record_and_correction_retries_are_idempotent(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    ledger = EvidenceLedger(path)
    payload = {"run_id": "run-1", "state": "error"}
    first = ledger.append_record(record_id="run-1", payload=payload, created_at=10)
    assert ledger.append_record(record_id="run-1", payload=payload, created_at=99) == first

    kwargs = {
        "correction_id": "correction-1",
        "target_record_id": "run-1",
        "relation": "corrects",
        "reason": "Post-run evidence changed the interpretation.",
        "evidence_refs": ["receipt.json"],
        "payload": {"state": "completed"},
        "created_at": 20,
    }
    correction = ledger.append_correction(**kwargs)
    assert ledger.append_correction(**kwargs) == correction
    assert len(_rows(path)) == 2


def test_reusing_an_id_for_different_evidence_fails(tmp_path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.jsonl")
    ledger.append_record(record_id="run-1", payload={"state": "error"})

    with pytest.raises(ValueError, match="different evidence"):
        ledger.append_record(record_id="run-1", payload={"state": "done"})


def test_preserve_existing_keeps_first_record_on_legacy_retry(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    ledger = EvidenceLedger(path)
    first = ledger.append_record(
        record_id="run-1",
        payload={"state": "error", "ts": 10},
    )

    returned = ledger.append_record(
        record_id="run-1",
        payload={"state": "error", "ts": 20},
        preserve_existing=True,
    )

    assert returned == first
    assert _rows(path) == [first]


def test_correction_requires_existing_target(tmp_path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.jsonl")

    with pytest.raises(KeyError, match="missing evidence"):
        ledger.append_correction(
            correction_id="correction-1",
            target_record_id="missing",
            relation="corrects",
            reason="New evidence.",
        )


def test_correction_can_target_legacy_experiment_row(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    path.write_text('{"run_id":"legacy-run","state":"error"}\n')
    ledger = EvidenceLedger(path)

    correction = ledger.append_correction(
        correction_id="legacy-correction",
        target_record_id="legacy-run",
        relation="annotates",
        reason="Preserve and explain the legacy result.",
    )

    assert correction["target_record_id"] == "legacy-run"
    assert len(ledger.history("legacy-run")) == 2


@pytest.mark.parametrize(
    "content",
    [
        '{"run_id":"torn"',
        '{"run_id":"ok"}\nnot-json\n',
        '["not-an-object"]\n',
    ],
)
def test_corrupt_ledger_fails_before_appending(content, tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    path.write_text(content)
    ledger = EvidenceLedger(path)

    with pytest.raises(ValueError, match="evidence ledger"):
        ledger.append_record(record_id="new", payload={"state": "done"})

    assert path.read_text() == content
