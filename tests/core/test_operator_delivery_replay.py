"""Canonical intake effects and durable source acknowledgements share one identity."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from argus.core import operator_context as context
from argus.core import operator_context_storage as storage
from argus.core.operator_context import (
    IntakeDecision,
    OperatorContextStore,
    OperatorDeliveryCapacityError,
    OperatorDeliveryClosed,
    OperatorDeliveryConflict,
    append_preference,
    apply_operator_delivery,
    close_operator_delivery_prefix,
    freeze_operator_intake,
)


def identity(sequence=1, *, stream="operator-queue", generation=1):
    return {"stream": stream, "generation": generation, "sequence": sequence}


def prefix(value):
    return {key: value[key] for key in ("stream", "generation", "sequence")}


def once_plan(root):
    return freeze_operator_intake(root, "withdraw the previously discussed constraint",
                                  IntakeDecision(kind="revocation"), mission_id="original-mission")


def ledger(root):
    return root / "operator_context.jsonl"


def replay_state(root):
    return json.loads(ledger(root).read_text().splitlines()[0])["state"][storage.DELIVERY_STATE_KEY]


def test_frozen_intake_preserves_target_and_mission_without_writes(tmp_path, monkeypatch):
    project = tmp_path / "user/projects/original"
    plan = freeze_operator_intake(project, "inspect the original issue", None, mission_id="mission-before")
    assert not project.exists()
    assert plan["effect"]["mission_id"] == "mission-before"
    assert json.loads(json.dumps(plan)) == plan
    with monkeypatch.context() as patch:
        patch.setattr(context, "_current_mission_id", lambda *_: pytest.fail("apply must not rebind the mission"))
        receipt = apply_operator_delivery(plan, identity())
    assert receipt["format"] == "operator-delivery-receipt-v2"
    assert receipt["plan"] == plan
    store = OperatorContextStore(project)
    assert len(store.project("engineer", mission_id="mission-before").directives) == 1
    assert store.project("engineer", mission_id="mission-after").directives == ()


def test_projection_failure_does_not_undo_the_committed_receipt(tmp_path, monkeypatch):
    plan = once_plan(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(context, "_write_cache", lambda *_: (_ for _ in ()).throw(OSError("cache unavailable")))
        receipt = apply_operator_delivery(plan, identity())
    assert not (tmp_path / context.PROJECTION_FILENAME).exists()
    assert apply_operator_delivery(plan, identity()) == receipt
    assert len(OperatorContextStore(tmp_path).records()) == 1


def test_once_consumption_compaction_and_closed_prefix_never_resurrect_effect(tmp_path):
    plan = once_plan(tmp_path)
    receipt = apply_operator_delivery(plan, identity())
    store = OperatorContextStore(tmp_path)
    assert len(store.project("engineer").directives) == 1
    store.compact()
    assert store.records() == []
    compacted = ledger(tmp_path).read_bytes()
    assert apply_operator_delivery(plan, identity()) == receipt
    assert ledger(tmp_path).read_bytes() == compacted
    assert store.project("engineer").directives == ()
    closed = close_operator_delivery_prefix(tmp_path, prefix(identity()))
    after_close = ledger(tmp_path).read_bytes()
    assert close_operator_delivery_prefix(tmp_path, prefix(identity())) == closed
    assert ledger(tmp_path).read_bytes() == after_close
    row = replay_state(tmp_path)["streams"]["operator-queue"]
    assert row["closed_sequence"] == 1 and row["receipts"] == {}
    with pytest.raises(OperatorDeliveryClosed):
        apply_operator_delivery(plan, identity())
    store.compact()
    with pytest.raises(OperatorDeliveryClosed):
        apply_operator_delivery(plan, identity())
    assert store.records() == []


def test_global_preference_and_revoke_replay_keep_original_target_and_revision(tmp_path):
    user = tmp_path / "user"
    project = user / "projects/original"
    preference = freeze_operator_intake(project, "prefer evidence", IntakeDecision(
        kind="preference", scope="global", preference_value="first choice"), mission_id="original")
    assert preference["target_root"] == str(user)
    first = apply_operator_delivery(preference, identity())
    later = append_preference(user, kind="workflow", value="later choice", scope="global", expected_revision=first["revision"])
    before = ledger(user).read_bytes()
    assert apply_operator_delivery(preference, identity()) == first
    assert ledger(user).read_bytes() == before
    revoke = freeze_operator_intake(project, "withdraw later choice", IntakeDecision(
        kind="revocation", scope="global", target_revision=later.revision))
    removed = apply_operator_delivery(revoke, identity(2))
    OperatorContextStore(user).compact()
    compacted = ledger(user).read_bytes()
    assert apply_operator_delivery(revoke, identity(2)) == removed
    assert ledger(user).read_bytes() == compacted
    assert removed["revision"] == 3 and first["revision"] == 1
    assert OperatorContextStore(user / "projects/other").project("planner").preferences == ()
    assert not ledger(project).exists()


@pytest.mark.parametrize("change", ["effect", "bound_target"])
def test_open_identity_rejects_different_effect_or_target(tmp_path, change):
    plan, delivery = once_plan(tmp_path), identity()
    apply_operator_delivery(plan, delivery)
    before = ledger(tmp_path).read_bytes()
    plan, delivery = deepcopy(plan), deepcopy(delivery)
    if change == "effect":
        plan["effect"]["text"] = "different operator authority"
    else:
        plan["target_root"] = str(tmp_path / "another")
    with pytest.raises(OperatorDeliveryConflict):
        OperatorContextStore(tmp_path).apply_operator_delivery(plan, delivery)
    assert ledger(tmp_path).read_bytes() == before


def test_generation_change_requires_closed_receipts_and_fences_older_generations(tmp_path):
    plan = once_plan(tmp_path)
    apply_operator_delivery(plan, identity())
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(OperatorDeliveryConflict, match="unclosed"):
        apply_operator_delivery(plan, identity(generation=2))
    assert ledger(tmp_path).read_bytes() == before
    close_operator_delivery_prefix(tmp_path, prefix(identity()))
    assert apply_operator_delivery(plan, identity(generation=2))["revision"] == 2
    with pytest.raises(OperatorDeliveryClosed):
        apply_operator_delivery(plan, identity(2, generation=1))
    close_operator_delivery_prefix(tmp_path, prefix(identity(generation=1)))
    assert len(replay_state(tmp_path)["streams"]) == 1


def test_close_cannot_skip_a_prefix_without_a_terminal_application_receipt(tmp_path):
    apply_operator_delivery(once_plan(tmp_path), identity())
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(OperatorDeliveryConflict, match="terminal"):
        close_operator_delivery_prefix(tmp_path, prefix(identity(2)))
    assert ledger(tmp_path).read_bytes() == before


def test_stream_capacity_keeps_closed_tombstones_and_preserves_pending_effect(tmp_path):
    plan = freeze_operator_intake(tmp_path, "no authority", IntakeDecision(kind="ephemeral"))
    for number in range(storage.MAX_OPERATOR_DELIVERY_STREAMS):
        delivery = identity(stream=f"queue-{number}")
        apply_operator_delivery(plan, delivery)
        close_operator_delivery_prefix(tmp_path, prefix(delivery))
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(OperatorDeliveryCapacityError, match="stream capacity"):
        apply_operator_delivery(plan, identity(stream="one-more-stream"))
    assert ledger(tmp_path).read_bytes() == before
    assert len(replay_state(tmp_path)["streams"]) == 64
    assert apply_operator_delivery(plan, identity(2, stream="queue-0"))["revision"] == 0


def test_receipt_capacity_can_be_released_only_by_a_source_acknowledged_prefix(tmp_path):
    plan = freeze_operator_intake(tmp_path, "no authority", IntakeDecision(kind="ephemeral"))
    for sequence in range(1, storage.MAX_OPERATOR_DELIVERY_RECEIPTS + 1):
        apply_operator_delivery(plan, identity(sequence))
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(OperatorDeliveryCapacityError, match="receipt capacity"):
        apply_operator_delivery(plan, identity(257))
    assert ledger(tmp_path).read_bytes() == before
    close_operator_delivery_prefix(tmp_path, prefix(identity(256)))
    assert apply_operator_delivery(plan, identity(257))["revision"] == 0
    assert len(replay_state(tmp_path)["streams"]["operator-queue"]["receipts"]) == 1


@pytest.mark.parametrize("changes", [
    {"generation": True}, {"sequence": 0}, {"sequence": 2 ** 63}, {"stream": "../other"}, {"extra": "field"},
])
def test_invalid_delivery_identity_never_creates_authority(tmp_path, changes):
    with pytest.raises(ValueError):
        apply_operator_delivery(once_plan(tmp_path), {**identity(), **changes})
    assert not ledger(tmp_path).exists()


@pytest.mark.parametrize("corruption", ["downgraded-format", "boolean-prefix", "foreign-receipt-sequence"])
def test_malformed_replay_metadata_is_rejected_before_projection(tmp_path, corruption):
    apply_operator_delivery(once_plan(tmp_path), identity())
    value = json.loads(ledger(tmp_path).read_text())
    if corruption == "downgraded-format":
        value["format"] = storage.FORMAT
    else:
        stream = value["state"][storage.DELIVERY_STATE_KEY]["streams"]["operator-queue"]
        if corruption == "boolean-prefix":
            stream["closed_sequence"] = True
        else:
            stream["receipts"]["1"]["identity"]["sequence"] = 2
    ledger(tmp_path).write_bytes(storage.encode(value))
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        OperatorContextStore(tmp_path).project("engineer")
    assert ledger(tmp_path).read_bytes() == before
