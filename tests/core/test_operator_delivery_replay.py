"""Canonical intake effects and durable source acknowledgements share one identity."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from argus.core import operator_context as context
from argus.core import operator_context_storage as storage
from argus.core.file_lock import FileLockCancelled, bounded_file_lock_wait
from argus.core.operator_context import (
    IntakeDecision,
    OperatorContextStore,
    OperatorDeliveryCapacityError,
    OperatorDeliveryClosed,
    OperatorDeliveryConflict,
    append_directive,
    append_preference,
    apply_operator_delivery,
    close_operator_delivery_prefix,
    freeze_operator_intake,
    operator_delivery_effect_digest,
)


def identity(sequence=1, *, stream="operator-queue", generation=1):
    raw = (json.dumps({"text": "original inbox line", "sequence": sequence}) + "\n").encode()
    return {"stream": stream, "generation": generation, "sequence": sequence,
            "digest": hashlib.sha256(raw).hexdigest()}


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
    expected = hashlib.sha256((json.dumps(plan, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()).hexdigest()
    assert operator_delivery_effect_digest(plan) == expected
    with monkeypatch.context() as patch:
        patch.setattr(context, "_current_mission_id", lambda *_: pytest.fail("apply must not rebind the mission"))
        receipt = apply_operator_delivery(plan, identity())
    assert receipt["effect_digest"] == expected
    store = OperatorContextStore(project)
    assert len(store.project("engineer", mission_id="mission-before").directives) == 1
    assert store.project("engineer", mission_id="mission-after").directives == ()


def test_empty_mission_is_frozen_instead_of_attaching_to_a_later_mission(tmp_path, monkeypatch):
    plan = freeze_operator_intake(tmp_path, "inspect the original issue", None)
    monkeypatch.setattr(context, "_current_mission_id", lambda *_: pytest.fail("late mission lookup"))
    apply_operator_delivery(plan, identity())
    assert plan["effect"]["mission_id"] == "__no_mission__"
    assert OperatorContextStore(tmp_path).project("engineer", mission_id="later").directives == ()


def test_checkpoint_failure_before_replace_has_neither_effect_nor_receipt(tmp_path, monkeypatch):
    append_directive(tmp_path, "Existing authority.", expected_revision=0)
    before = ledger(tmp_path).read_bytes()
    original = storage.os.replace

    def fail_replace(source, target):
        if Path(target) == ledger(tmp_path):
            raise OSError("canonical checkpoint unavailable")
        return original(source, target)

    plan = once_plan(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError, match="checkpoint unavailable"):
            apply_operator_delivery(plan, identity())
    assert ledger(tmp_path).read_bytes() == before
    assert len(OperatorContextStore(tmp_path).records()) == 1
    assert apply_operator_delivery(plan, identity())["revision"] == 2
    header = json.loads(ledger(tmp_path).read_text().splitlines()[0])
    assert header["format"] == storage.DELIVERY_FORMAT
    assert header["records"][-1]["revision"] == header["state"][storage.DELIVERY_STATE_KEY]["streams"]["operator-queue"]["receipts"]["1"]["revision"] == 2


@pytest.mark.parametrize("empty_effect", [False, True])
def test_process_exit_after_checkpoint_before_receipt_return_replays_once(tmp_path, empty_effect):
    plan = freeze_operator_intake(tmp_path, "transient response", IntakeDecision(kind="ephemeral")) if empty_effect else once_plan(tmp_path)
    payload = tmp_path / "invocation.json"
    payload.write_text(json.dumps({"plan": plan, "identity": identity()}))
    source = Path(context.__file__).resolve().parents[2]
    script = """import json, os, sys
sys.path.insert(0, sys.argv[1])
from argus.core import operator_context as context
value=json.load(open(sys.argv[2]))
original=context.write_checkpoint
def crash_after_checkpoint(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(73)
context.write_checkpoint=crash_after_checkpoint
context.apply_operator_delivery(value['plan'], value['identity'])
"""
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "ARGUS_SKILL_HOME": str(tmp_path / "isolated-home")}
    process = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(source), str(payload)],
                             cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert process.returncode == 73, process.stderr
    committed = ledger(tmp_path).read_bytes()
    durable = replay_state(tmp_path)["streams"]["operator-queue"]["receipts"]["1"]
    assert apply_operator_delivery(plan, identity()) == durable
    assert ledger(tmp_path).read_bytes() == committed
    assert durable["revision"] == (0 if empty_effect else 1)
    assert (tmp_path / context.OWNERSHIP_FILENAME).exists()
    assert len(OperatorContextStore(tmp_path).records()) == (0 if empty_effect else 1)
    ledger(tmp_path).unlink()
    with pytest.raises(ValueError, match="established operator context source is missing"):
        apply_operator_delivery(plan, identity())


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


@pytest.mark.parametrize("change", ["digest", "effect", "bound_target"])
def test_open_identity_rejects_different_digest_effect_or_target(tmp_path, change):
    plan, delivery = once_plan(tmp_path), identity()
    apply_operator_delivery(plan, delivery)
    before = ledger(tmp_path).read_bytes()
    plan, delivery = deepcopy(plan), deepcopy(delivery)
    if change == "digest":
        delivery["digest"] = "f" * 64
    elif change == "effect":
        plan["effect"]["text"] = "different operator authority"
    else:
        plan["target_root"] = str(tmp_path / "another")
    with pytest.raises(OperatorDeliveryConflict):
        OperatorContextStore(tmp_path).apply_operator_delivery(plan, delivery)
    assert ledger(tmp_path).read_bytes() == before


def test_equal_text_with_distinct_deliveries_is_not_once_idempotency(tmp_path):
    plan = once_plan(tmp_path)
    first = apply_operator_delivery(plan, identity())
    second = apply_operator_delivery(plan, identity(2))
    assert first["revision"] == 1 and second["revision"] == 2
    assert len(OperatorContextStore(tmp_path).project("engineer").directives) == 2


def test_parallel_duplicate_delivery_has_one_canonical_revision(tmp_path):
    plan = once_plan(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: apply_operator_delivery(plan, identity()), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert receipts[0]["revision"] == 1
    assert len(OperatorContextStore(tmp_path).records()) == 1


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
    {"generation": True}, {"sequence": 0}, {"sequence": 2 ** 63}, {"stream": "../other"}, {"digest": "not-a-digest"},
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
    value.pop("checkpoint_digest")
    value["checkpoint_digest"] = hashlib.sha256(storage.encode(value)).hexdigest()
    ledger(tmp_path).write_bytes(storage.encode(value))
    before = ledger(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        OperatorContextStore(tmp_path).project("engineer")
    assert ledger(tmp_path).read_bytes() == before


def test_cancellation_before_target_lock_leaves_delivery_replayable(tmp_path):
    plan = once_plan(tmp_path)
    with bounded_file_lock_wait(timeout_seconds=1, cancelled=lambda: True):
        with pytest.raises(FileLockCancelled):
            apply_operator_delivery(plan, identity())
    assert not ledger(tmp_path).exists()
    assert apply_operator_delivery(plan, identity())["revision"] == 1


def test_ordinary_checkpoints_keep_v2_until_the_first_delivery(tmp_path):
    append_directive(tmp_path, "Ordinary existing policy.", expected_revision=0)
    OperatorContextStore(tmp_path).compact()
    assert json.loads(ledger(tmp_path).read_text())["format"] == storage.FORMAT
    apply_operator_delivery(once_plan(tmp_path), identity())
    assert json.loads(ledger(tmp_path).read_text())["format"] == storage.DELIVERY_FORMAT
