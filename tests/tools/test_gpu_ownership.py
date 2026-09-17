from __future__ import annotations

import copy

import pytest

from argus.tools.gpu_ownership import (
    establish_baseline,
    evaluate_snapshot,
)


def _policy() -> dict:
    return {
        "protected_gpu_indices": ["0"],
        "training_gpu_indices": ["4"],
        "campaign_roots": ["/campaign"],
        "protected_owner_roots": ["/protected-owner"],
        "stale_process_grace_seconds": 120,
    }


def _process(
    *,
    index: str,
    gpu_uuid: str,
    pid: int,
    process_name: str,
    campaign_owned: bool = False,
    protected_owner_owned: bool = False,
    metadata_available: bool = True,
    process_present: bool = True,
    stale: bool = False,
) -> dict:
    return {
        "gpu_uuid": gpu_uuid,
        "physical_index": index,
        "pid": pid,
        "process_name": process_name,
        "used_memory": "1 MiB",
        "metadata_available": metadata_available,
        "process_present_after_metadata_read": process_present,
        "metadata_error": "",
        "ppid": 1 if metadata_available else None,
        "start_time_ticks": str(pid * 10) if metadata_available else None,
        "executable": f"/owner/{process_name}" if metadata_available else None,
        "cwd": "/campaign" if campaign_owned else "/protected-owner",
        "cmdline": process_name if metadata_available else None,
        "cmdline_sha256": f"sha-{pid}" if metadata_available else None,
        "campaign_owned": campaign_owned,
        "campaign_ownership_reasons": ["self:cwd:/campaign"]
        if campaign_owned
        else [],
        "protected_owner_owned": protected_owner_owned,
        "protected_owner_reasons": ["self:cwd:/protected-owner"]
        if protected_owner_owned
        else [],
        "stale_nvidia_process": stale,
    }


def _protected_process() -> dict:
    return _process(
        index="0",
        gpu_uuid="GPU-protected",
        pid=100,
        process_name="protected.py",
        protected_owner_owned=True,
    )


def _snapshot(*processes: dict) -> dict:
    return {
        "time_utc": "2026-08-16T00:00:00+00:00",
        "gpus": [
            {"index": "0", "uuid": "GPU-protected"},
            {"index": "4", "uuid": "GPU-training"},
        ],
        "compute_processes": list(processes),
    }


def _baseline() -> dict:
    return establish_baseline(_snapshot(_protected_process()), _policy())


def test_known_campaign_process_can_age_into_stale_driver_row() -> None:
    baseline = _baseline()
    live_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        campaign_owned=True,
    )
    receipt, state = evaluate_snapshot(
        _snapshot(_protected_process(), live_training),
        baseline,
        now_epoch=1_000,
    )
    assert receipt["gpu_guard_ok"] is True
    assert len(state["recent_campaign_processes"]) == 1

    stale_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        metadata_available=False,
        process_present=False,
        stale=True,
    )
    receipt, _ = evaluate_snapshot(
        _snapshot(_protected_process(), stale_training),
        baseline,
        previous_state=state,
        now_epoch=1_030,
    )
    assert receipt["gpu_guard_ok"] is True
    assert receipt["authorized_stale_training_processes"][0]["stale_age_seconds"] == 30


def test_unknown_stale_driver_row_fails_closed() -> None:
    stale_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=999,
        process_name="unknown.py",
        metadata_available=False,
        process_present=False,
        stale=True,
    )

    receipt, _ = evaluate_snapshot(
        _snapshot(_protected_process(), stale_training),
        _baseline(),
        now_epoch=1_000,
    )

    assert receipt["gpu_guard_ok"] is False
    assert receipt["authorized_stale_training_processes"] == []
    assert receipt["unauthorized_processes_on_training_gpus"] == [stale_training]


def test_stale_authorization_expires() -> None:
    baseline = _baseline()
    live_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        campaign_owned=True,
    )
    _, state = evaluate_snapshot(
        _snapshot(_protected_process(), live_training),
        baseline,
        now_epoch=1_000,
    )
    stale_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        metadata_available=False,
        process_present=False,
        stale=True,
    )

    receipt, _ = evaluate_snapshot(
        _snapshot(_protected_process(), stale_training),
        baseline,
        previous_state=state,
        now_epoch=1_121,
    )

    assert receipt["gpu_guard_ok"] is False


def test_stale_authorization_is_bound_to_baseline_digest() -> None:
    live_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        campaign_owned=True,
    )
    _, state = evaluate_snapshot(
        _snapshot(_protected_process(), live_training),
        _baseline(),
        now_epoch=1_000,
    )
    replacement_owner = _protected_process()
    replacement_owner["pid"] = 101
    replacement_owner["start_time_ticks"] = "1010"
    replacement_baseline = establish_baseline(
        _snapshot(replacement_owner),
        _policy(),
    )
    stale_training = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=200,
        process_name="train.py",
        metadata_available=False,
        process_present=False,
        stale=True,
    )

    receipt, next_state = evaluate_snapshot(
        _snapshot(replacement_owner, stale_training),
        replacement_baseline,
        previous_state=state,
        now_epoch=1_030,
    )

    assert receipt["previous_state_baseline_matched"] is False
    assert receipt["gpu_guard_ok"] is False
    assert next_state["baseline_sha256"] == replacement_baseline["baseline_sha256"]


def test_live_unverifiable_training_process_fails_closed() -> None:
    unowned = _process(
        index="4",
        gpu_uuid="GPU-training",
        pid=300,
        process_name="foreign.py",
        metadata_available=False,
        process_present=True,
        stale=False,
    )

    receipt, _ = evaluate_snapshot(
        _snapshot(_protected_process(), unowned),
        _baseline(),
    )

    assert receipt["gpu_guard_ok"] is False
    assert receipt["unauthorized_processes_on_training_gpus"] == [unowned]


def test_protected_owner_stop_or_replacement_fails() -> None:
    missing_receipt, _ = evaluate_snapshot(_snapshot(), _baseline())
    assert missing_receipt["gpu_guard_ok"] is False
    assert missing_receipt["missing_protected_baseline_processes"]

    replacement = copy.deepcopy(_protected_process())
    replacement["pid"] = 101
    replacement["start_time_ticks"] = "1010"
    replacement_receipt, _ = evaluate_snapshot(
        _snapshot(replacement),
        _baseline(),
    )
    assert replacement_receipt["gpu_guard_ok"] is False
    assert replacement_receipt["missing_protected_baseline_processes"]
    assert replacement_receipt["unexpected_protected_gpu_processes"]


def test_campaign_process_on_protected_gpu_fails() -> None:
    crossover = _process(
        index="0",
        gpu_uuid="GPU-protected",
        pid=400,
        process_name="train.py",
        campaign_owned=True,
    )

    receipt, _ = evaluate_snapshot(
        _snapshot(_protected_process(), crossover),
        _baseline(),
    )

    assert receipt["gpu_guard_ok"] is False
    assert receipt["campaign_processes_on_protected_gpus"] == [crossover]


def test_baseline_refuses_empty_or_foreign_protected_gpu() -> None:
    with pytest.raises(RuntimeError, match="occupy every protected GPU"):
        establish_baseline(_snapshot(), _policy())

    foreign = _process(
        index="0",
        gpu_uuid="GPU-protected",
        pid=500,
        process_name="foreign.py",
    )
    with pytest.raises(RuntimeError, match="invalid protected baseline"):
        establish_baseline(_snapshot(foreign), _policy())


def test_tampered_baseline_is_rejected() -> None:
    baseline = _baseline()
    baseline["policy"]["training_gpu_indices"] = ["5"]

    with pytest.raises(ValueError, match="baseline_sha256"):
        evaluate_snapshot(_snapshot(_protected_process()), baseline)
