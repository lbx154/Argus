from __future__ import annotations

import pytest

from argus_skill.apps import _inbox


def test_raw_drain_cannot_bypass_durable_acceptance(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "change direction", source="test")
    with pytest.raises(_inbox.InboxProtocolError, match="claim/accept/ACK"):
        _inbox.drain_inbox_messages(tmp_path)
    assert _inbox.count_pending_inbox_messages(tmp_path) == 1


def test_stage_targeted_message_waits_without_blocking_generic_guidance(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "profile only after baseline", source="test", stage="optimize")
    _inbox.queue_inbox_message(tmp_path, "stop current run", source="test")
    assert _inbox.count_pending_inbox_messages(tmp_path) == 2
    claim = _inbox.claim_inbox_message(tmp_path, current_stage="baseline")
    assert claim is not None and claim.text == "stop current run"
    assert _inbox.claim_inbox_message(tmp_path, current_stage="baseline") is None
    staged = _inbox.claim_inbox_message(tmp_path, current_stage="optimize")
    assert staged is not None and staged.text == "profile only after baseline"


def test_stage_targeted_event_renders_scope(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "run profiler", source="test", stage="optimize")
    event = __import__("json").loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    rendered = _inbox.format_inbox_event(event)
    assert rendered is not None and "stage=optimize" in rendered


def test_advisory_event_failure_does_not_report_committed_enqueue_failed(tmp_path, monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("event unavailable")
    monkeypatch.setattr(_inbox.JsonlEventSink, "append", fail)
    _inbox.queue_inbox_message(tmp_path, "durably accepted", source="test")
    assert _inbox.count_pending_inbox_messages(tmp_path) == 1


def test_failed_acceptance_leaves_same_message_pending_after_recovery(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "change direction", source="test")
    claim = _inbox.claim_inbox_message(tmp_path)
    assert claim is not None
    with pytest.raises(_inbox.InboxProtocolError, match="frozen decision"):
        _inbox.accept_inbox_claim(tmp_path, claim)
    _inbox.release_inbox_claim(tmp_path, claim)
    recovered = _inbox.claim_inbox_message(tmp_path)
    assert recovered is not None and recovered.identity == claim.identity
    assert _inbox.count_pending_inbox_messages(tmp_path) == 1


def test_source_ack_preserves_delivery_until_explicit_settlement(tmp_path) -> None:
    _inbox.queue_inbox_message(tmp_path, "change direction", source="test")
    claim = _inbox.claim_inbox_message(tmp_path)
    assert claim is not None
    claim = _inbox.freeze_inbox_decision(tmp_path, claim, decision={"effect": None}, target_root=None, transient_text=claim.text)
    claim = _inbox.accept_inbox_claim(tmp_path, claim)
    claim = _inbox.acknowledge_inbox_claim(tmp_path, claim)
    _inbox.release_inbox_claim(tmp_path, claim)
    recovered = _inbox.claim_inbox_message(tmp_path)
    assert recovered is not None and recovered.acknowledged
    assert recovered.identity == claim.identity and recovered.transient_text == "change direction"
    _inbox.settle_inbox_claim(tmp_path, recovered)
    assert _inbox.count_pending_inbox_messages(tmp_path) == 0
