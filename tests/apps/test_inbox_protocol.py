from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from argus.apps import _inbox_protocol as inbox


def put(root, text="message", stage=""):
    inbox.enqueue_inbox_message(root, text, source="test", stage=stage)


def take(root, **kwargs):
    claim = inbox.claim_inbox_message(root, **kwargs)
    assert claim is not None
    return claim


def transient(root, claim):
    claim = inbox.freeze_inbox_decision(root, claim, decision={"kind": "transient"}, target_root=None, transient_text=claim.text)
    claim = inbox.accept_inbox_claim(root, claim)
    return inbox.acknowledge_inbox_claim(root, claim)


def receipt(claim, target):
    return {"format": "operator-delivery-receipt-v2", "identity": claim.identity,
            "target_root": str(target.resolve()), "plan": claim.decision, "revision": 17}


def test_transient_ack_retains_original_envelope_and_blocks_next(tmp_path):
    put(tmp_path, "first")
    put(tmp_path, "second")
    original = take(tmp_path, current_stage="code", mission_id="old")
    claim = transient(tmp_path, original)
    assert inbox.count_durable_inbox_messages(tmp_path) == 2
    inbox.release_inbox_claim(tmp_path, claim)
    assert inbox.claim_inbox_message(tmp_path, current_stage="code", mission_id="new") is None
    status = inbox.pending_inbox_status(tmp_path, current_stage="code", mission_id="new")
    assert status[0]["reason"] == "original_consumer_mission_stage_required"
    recovered = take(tmp_path, current_stage="code", mission_id="old")
    assert recovered.acknowledged and recovered.identity == original.identity
    assert recovered.transient_text == "first" and recovered.owner != original.owner
    inbox.settle_inbox_claim(tmp_path, recovered)
    assert take(tmp_path, mission_id="new").text == "second"


def test_frozen_canonical_recovery_keeps_exact_plan_and_binding(tmp_path):
    put(tmp_path, stage="code")
    original = take(tmp_path, current_stage="code", mission_id="old")
    target = tmp_path / "global"
    plan = {"version": 1, "target_root": str(target), "effect": {"text": "do once", "mission": "old"}}
    frozen = inbox.freeze_inbox_decision(tmp_path, original, decision=plan, target_root=target)
    inbox.release_inbox_claim(tmp_path, frozen)
    recovered = take(tmp_path, consumer="supervisor", current_stage="review", mission_id="new")
    assert (recovered.consumer, recovered.consumer_stage, recovered.mission_id) == ("engineer", "code", "old")
    assert recovered.decision == plan and recovered.target_root == str(target.resolve())
    with pytest.raises(inbox.InboxProtocolError, match="immutable"):
        inbox.freeze_inbox_decision(tmp_path, recovered, decision=plan | {"effect": {"text": "changed"}}, target_root=target)
    for invalid in [receipt(recovered, target) | {"identity": original.identity | {"sequence": 99}}, receipt(recovered, tmp_path)]:
        with pytest.raises(inbox.InboxProtocolError, match="identity and target"):
            inbox.accept_inbox_claim(tmp_path, recovered, receipt=invalid)
    accepted = inbox.accept_inbox_claim(tmp_path, recovered, receipt=receipt(recovered, target))
    acked = inbox.acknowledge_inbox_claim(tmp_path, accepted)
    inbox.release_inbox_claim(tmp_path, acked)
    closing = take(tmp_path, consumer="third", mission_id="third")
    assert closing.acknowledged and closing.receipt == receipt(closing, target)
    with pytest.raises(inbox.InboxProtocolError, match="receipt must close"):
        inbox.settle_inbox_claim(tmp_path, closing)
    inbox.settle_inbox_claim(tmp_path, closing, canonical_closed=True)
    with pytest.raises(inbox.InboxLeaseLost):
        inbox.acknowledge_inbox_claim(tmp_path, closing)


def test_ack_cannot_precede_durable_acceptance(tmp_path):
    put(tmp_path)
    claim = take(tmp_path)
    with pytest.raises(inbox.InboxProtocolError, match="before durable acceptance"):
        inbox.acknowledge_inbox_claim(tmp_path, claim)
    with pytest.raises(inbox.InboxProtocolError, match="frozen decision"):
        inbox.accept_inbox_claim(tmp_path, claim)
    assert inbox.count_durable_inbox_messages(tmp_path) == 1


def test_partial_legacy_never_acknowledged_and_completion_is_discoverable(tmp_path):
    path = tmp_path / "inbox.jsonl"
    path.write_bytes(b'{"text":"not complete')
    with pytest.raises(inbox.InboxPartialLine):
        inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    assert not (tmp_path / "inbox.offset").exists()
    with path.open("ab") as handle:
        handle.write(b' yet"}\n')
        handle.flush()
        os.fsync(handle.fileno())
    inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    assert take(tmp_path).text == "not complete yet"


@pytest.mark.parametrize("offset", [1, 999, -1])
def test_truncated_or_split_legacy_cursor_is_not_reset(tmp_path, offset):
    path = tmp_path / "inbox.jsonl"
    path.write_bytes(b'{"text":"still pending"}\n')
    (tmp_path / "inbox.offset").write_text(str(offset))
    with pytest.raises(inbox.InboxProtocolError):
        inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    assert path.is_file()


def test_database_missing_or_replaced_cannot_reset_generation(tmp_path):
    put(tmp_path / "a")
    put(tmp_path / "b")
    database = tmp_path / "a" / inbox.PROTOCOL_DIR / "queue.sqlite3"
    other = tmp_path / "b" / inbox.PROTOCOL_DIR / "queue.sqlite3"
    saved = database.read_bytes()
    database.unlink()
    with pytest.raises(inbox.InboxProtocolError, match="missing"):
        take(tmp_path / "a")
    database.write_bytes(other.read_bytes())
    with pytest.raises(inbox.InboxProtocolError, match="generation"):
        take(tmp_path / "a")
    database.write_bytes(saved)
    assert take(tmp_path / "a").text == "message"


def test_old_unknown_stage_writer_is_detected_without_import(tmp_path):
    put(tmp_path)
    bad = tmp_path / "inbox.old-stage.jsonl"
    bad.write_text('{"text":"old writer"}\n')
    with pytest.raises(inbox.InboxProtocolError, match="legacy writer"):
        take(tmp_path)
    assert bad.is_file()


def test_pending_and_closed_stream_pressure_never_evict(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "MAX_PENDING_MESSAGES", 2)
    monkeypatch.setattr(inbox, "MAX_STREAMS", 2)
    put(tmp_path, "a")
    put(tmp_path, "b")
    with pytest.raises(inbox.InboxPressure):
        put(tmp_path, "c")
    for _ in range(2):
        inbox.settle_inbox_claim(tmp_path, transient(tmp_path, take(tmp_path)))
    put(tmp_path, "d", stage="second")
    inbox.settle_inbox_claim(tmp_path, transient(tmp_path, take(tmp_path, current_stage="second")))
    with pytest.raises(inbox.InboxPressure, match="closed streams"):
        put(tmp_path, "refused", stage="third")
    put(tmp_path, "next")
    assert take(tmp_path).identity["sequence"] == 3


def test_busy_writer_has_short_bounded_wait_and_message_survives(tmp_path):
    put(tmp_path)
    db = sqlite3.connect(tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3")
    db.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(inbox.InboxBusy):
            take(tmp_path)
    finally:
        db.rollback()
        db.close()
    assert time.monotonic() - started < 1.5
    assert take(tmp_path).text == "message"


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), 301])
def test_lease_bound_is_finite_and_explicit(tmp_path, duration):
    with pytest.raises(ValueError):
        inbox.claim_inbox_message(tmp_path, lease_seconds=duration)


def test_interrupted_migration_recovers_same_complete_lines(tmp_path, monkeypatch):
    raw = b'{"text":"first"}\n{"text":"second"}\n'
    (tmp_path / "inbox.jsonl").write_bytes(raw)
    original = inbox._insert
    def fail(db, root, stage, payload, **kwargs):
        original(db, root, stage, payload, **kwargs)
        raise OSError("migration interrupted after insert")
    monkeypatch.setattr(inbox, "_insert", fail)
    with pytest.raises(OSError):
        inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    assert (tmp_path / inbox.PROTOCOL_DIR / "legacy-.jsonl").read_bytes() == raw
    with pytest.raises(inbox.InboxMigrationRequired):
        take(tmp_path)
    monkeypatch.setattr(inbox, "_insert", original)
    inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    first = take(tmp_path)
    assert first.text == "first" and first.identity["sequence"] == 1
    inbox.settle_inbox_claim(tmp_path, transient(tmp_path, first))
    assert take(tmp_path).text == "second"


def test_receipt_cannot_ack_a_different_frozen_effect(tmp_path):
    put(tmp_path)
    target = tmp_path / "global"
    claim = take(tmp_path)
    claim = inbox.freeze_inbox_decision(tmp_path, claim, decision={"version": 1, "target_root": str(target), "effect": {"text": "original"}}, target_root=target)
    bad = receipt(claim, target)
    bad["plan"] = bad["plan"] | {"effect": {"text": "other"}}
    with pytest.raises(inbox.InboxProtocolError, match="frozen effect"):
        inbox.accept_inbox_claim(tmp_path, claim, receipt=bad)
    assert inbox.count_durable_inbox_messages(tmp_path) == 1


def test_generation_change_and_raw_corruption_fail_closed(tmp_path):
    put(tmp_path)
    path = tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE streams SET generation=2")
    with pytest.raises(inbox.InboxProtocolError, match="generation"):
        take(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE streams SET generation=1")
        db.execute("UPDATE messages SET raw=?", (b'{"text":"changed"}\n',))
    with pytest.raises(inbox.InboxProtocolError, match="complete line or byte range"):
        take(tmp_path)


def test_migration_preserves_timestamp_of_already_consumed_input(tmp_path):
    raw = b'{"text":"already read","ts":123.5}\n'
    (tmp_path / "inbox.jsonl").write_bytes(raw)
    (tmp_path / "inbox.offset").write_text(str(len(raw)))
    assert inbox.latest_durable_inbox_timestamp(tmp_path) is None
    inbox.migrate_legacy_inbox(tmp_path, writers_stopped=True)
    assert inbox.count_durable_inbox_messages(tmp_path) == 0
    assert inbox.latest_durable_inbox_timestamp(tmp_path) == 123.5


def test_legacy_readonly_counts_complete_valid_pending_without_migration(tmp_path):
    first = b'{"text":"read"}\n'
    raw = first + b'{"text":"pending"}\ninvalid\n{"text":"partial'
    path = tmp_path / "inbox.jsonl"
    path.write_bytes(raw)
    (tmp_path / "inbox.offset").write_text(str(len(first)))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert inbox.count_durable_inbox_messages(tmp_path) == 1
    assert inbox.pending_inbox_status(tmp_path) == [{"stage": "", "count": 1, "reason": "migration_required", "partial": True}]
    with pytest.raises(inbox.InboxMigrationRequired):
        take(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_active_count_and_status_are_read_only_while_writer_holds_transaction(tmp_path):
    put(tmp_path)
    database = tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3"
    before = database.stat().st_mtime_ns
    with sqlite3.connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        assert inbox.count_durable_inbox_messages(tmp_path) == 1
        assert inbox.pending_inbox_status(tmp_path)[0]["reason"] == "available"
        db.rollback()
    assert database.stat().st_mtime_ns == before


def test_corrupt_redundant_text_cannot_classify_under_original_identity(tmp_path):
    put(tmp_path, "original authorized text")
    dbpath = tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("UPDATE messages SET text='different instruction'")
    classified = []
    with pytest.raises(inbox.InboxProtocolError, match="original line"):
        claim = take(tmp_path)
        classified.append(claim.text)
    assert classified == []
    assert inbox.count_durable_inbox_messages(tmp_path) == 1
    with sqlite3.connect(dbpath) as db:
        assert db.execute("SELECT ack_sequence FROM streams").fetchone()[0] == 0


@pytest.mark.parametrize("column,value", [
    ("mission_id", "changed mission"), ("consumer_stage", "changed stage"),
    ("consumer", "supervisor"), ("transient_text", "different transient"),
    ("decision", '{"kind":"different"}'), ("decision", "[]"),
    ("target_root", "/different/target"), ("binding_digest", '{"changed":true}'),
    ("frozen_digest", '{"changed":true}'), ("accepted", 0),
])
def test_corrupt_frozen_transient_or_binding_never_delivers(tmp_path, column, value):
    put(tmp_path)
    claim = transient(tmp_path, take(tmp_path, mission_id="original"))
    inbox.release_inbox_claim(tmp_path, claim)
    dbpath = tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute(f"UPDATE messages SET {column}=?", (value,))
    with pytest.raises(inbox.InboxProtocolError):
        take(tmp_path, mission_id="original")
    assert inbox.count_durable_inbox_messages(tmp_path) == 1


@pytest.mark.parametrize("column,value", [
    ("receipt", "[]"), ("receipt", '{"revision":999}'), ("receipt", None),
])
def test_corrupt_accepted_canonical_receipt_cannot_ack_or_close(tmp_path, column, value):
    put(tmp_path)
    target = tmp_path / "global"
    claim = take(tmp_path)
    plan = {"version": 1, "target_root": str(target), "effect": {"text": "once"}}
    claim = inbox.freeze_inbox_decision(tmp_path, claim, decision=plan, target_root=target)
    claim = inbox.accept_inbox_claim(tmp_path, claim, receipt=receipt(claim, target))
    inbox.release_inbox_claim(tmp_path, claim)
    with sqlite3.connect(tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3") as db:
        db.execute(f"UPDATE messages SET {column}=?", (value,))
    with pytest.raises(inbox.InboxProtocolError):
        take(tmp_path)
    with sqlite3.connect(tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3") as db:
        assert db.execute("SELECT ack_sequence FROM streams").fetchone()[0] == 0


@pytest.mark.parametrize("revision", [-1, True, "17", None])
def test_canonical_receipt_revision_requires_actual_nonnegative_integer(tmp_path, revision):
    put(tmp_path)
    target = tmp_path / "global"
    claim = take(tmp_path)
    claim = inbox.freeze_inbox_decision(tmp_path, claim, decision={"version": 1, "target_root": str(target), "effect": {"text": "once"}}, target_root=target)
    with pytest.raises(inbox.InboxProtocolError, match="revision"):
        inbox.accept_inbox_claim(tmp_path, claim, receipt=receipt(claim, target) | {"revision": revision})


def test_source_v1_marker_is_not_silently_upgraded(tmp_path):
    put(tmp_path)
    marker = tmp_path / inbox.PROTOCOL_DIR / "protocol.json"
    old = json.loads(marker.read_text()) | {"version": 1}
    marker.write_text(json.dumps(old))
    with sqlite3.connect(tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3") as db:
        db.execute("UPDATE metadata SET value='1' WHERE key='version'")
    before = marker.read_bytes()
    with pytest.raises(inbox.InboxProtocolError, match="version"):
        take(tmp_path)
    assert marker.read_bytes() == before


def test_corrupt_envelope_ack_flag_does_not_replace_durable_source_prefix(tmp_path):
    put(tmp_path)
    claim = take(tmp_path)
    claim = inbox.freeze_inbox_decision(tmp_path, claim, decision={"kind": "transient"}, target_root=None, transient_text=claim.text)
    claim = inbox.accept_inbox_claim(tmp_path, claim)
    inbox.release_inbox_claim(tmp_path, claim)
    with sqlite3.connect(tmp_path / inbox.PROTOCOL_DIR / "queue.sqlite3") as db:
        db.execute("UPDATE messages SET acknowledged=1")
    with pytest.raises(inbox.InboxProtocolError, match="source prefix"):
        take(tmp_path)
