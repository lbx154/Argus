"""Fail-closed GPU ownership checks based on process identity and ancestry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.file_lock import exclusive_file_lock
from ..core.process_identity import read_process_metadata

IDENTITY_FIELDS = (
    "gpu_uuid",
    "physical_index",
    "pid",
    "process_name",
    "executable",
    "cwd",
    "start_time_ticks",
    "cmdline_sha256",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_text(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    ).stdout


def _path_is_within(value: object, root: Path) -> bool:
    if not value:
        return False
    try:
        Path(str(value)).resolve(strict=False).relative_to(
            root.resolve(strict=False)
        )
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _ownership_reasons(
    pid: int,
    metadata: dict[str, Any],
    roots: Iterable[Path],
    *,
    proc_root: Path,
    read_metadata: Callable[..., dict[str, Any]] = read_process_metadata,
) -> list[str]:
    normalized_roots = tuple(root.resolve(strict=False) for root in roots)
    reasons: list[str] = []
    current_pid = pid
    current = metadata
    visited: set[int] = set()
    depth = 0
    while current_pid > 1 and current_pid not in visited and depth < 64:
        visited.add(current_pid)
        prefix = "self" if depth == 0 else f"ancestor:{current_pid}"
        for root in normalized_roots:
            if _path_is_within(current.get("cwd"), root):
                reasons.append(f"{prefix}:cwd:{root}")
            if _path_is_within(current.get("executable"), root):
                reasons.append(f"{prefix}:executable:{root}")
        parent = current.get("ppid")
        if not isinstance(parent, int) or parent <= 1:
            break
        current_pid = parent
        current = read_metadata(current_pid, proc_root=proc_root)
        if not current.get("metadata_available"):
            break
        depth += 1
    return reasons


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate the project-owned GPU policy."""
    protected = {str(value) for value in policy.get("protected_gpu_indices", [])}
    training = {str(value) for value in policy.get("training_gpu_indices", [])}
    campaign_roots = [
        str(Path(value).expanduser().resolve(strict=False))
        for value in policy.get("campaign_roots", [])
        if str(value).strip()
    ]
    protected_owner_roots = [
        str(Path(value).expanduser().resolve(strict=False))
        for value in policy.get("protected_owner_roots", [])
        if str(value).strip()
    ]
    if not protected:
        raise ValueError("protected_gpu_indices must be non-empty")
    if not training:
        raise ValueError("training_gpu_indices must be non-empty")
    if protected & training:
        raise ValueError("protected and training GPU indices must be disjoint")
    if not campaign_roots:
        raise ValueError("campaign_roots must be non-empty")
    if not protected_owner_roots:
        raise ValueError("protected_owner_roots must be non-empty")
    grace = float(policy.get("stale_process_grace_seconds", 120))
    if grace <= 0:
        raise ValueError("stale_process_grace_seconds must be positive")
    return {
        "protected_gpu_indices": sorted(protected),
        "training_gpu_indices": sorted(training),
        "campaign_roots": campaign_roots,
        "protected_owner_roots": protected_owner_roots,
        "stale_process_grace_seconds": grace,
    }


def capture_gpu_snapshot(
    policy: dict[str, Any],
    *,
    proc_root: Path = Path("/proc"),
    run_text: Callable[[list[str]], str] = _run_text,
) -> dict[str, Any]:
    """Capture GPUs and compute processes, classifying only deterministic owners."""
    normalized = validate_policy(policy)
    gpu_lines = run_text(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader",
        ]
    )
    process_lines = run_text(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
            "--format=csv,noheader",
        ]
    )
    gpus: list[dict[str, str]] = []
    uuid_to_index: dict[str, str] = {}
    for line in gpu_lines.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 6:
            raise RuntimeError(f"unexpected nvidia-smi GPU row: {line!r}")
        gpu = {
            "index": parts[0],
            "uuid": parts[1],
            "name": parts[2],
            "memory_total": parts[3],
            "memory_used": parts[4],
            "utilization_gpu": parts[5],
        }
        gpus.append(gpu)
        uuid_to_index[gpu["uuid"]] = gpu["index"]

    campaign_roots = [Path(value) for value in normalized["campaign_roots"]]
    protected_roots = [
        Path(value) for value in normalized["protected_owner_roots"]
    ]
    processes: list[dict[str, Any]] = []
    for line in process_lines.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            raise RuntimeError(f"unexpected nvidia-smi process row: {line!r}")
        pid = int(parts[1])
        metadata = read_process_metadata(pid, proc_root=proc_root)
        campaign_reasons = _ownership_reasons(
            pid,
            metadata,
            campaign_roots,
            proc_root=proc_root,
        )
        protected_reasons = _ownership_reasons(
            pid,
            metadata,
            protected_roots,
            proc_root=proc_root,
        )
        processes.append(
            {
                "gpu_uuid": parts[0],
                "physical_index": uuid_to_index.get(parts[0], "unknown"),
                "pid": pid,
                "process_name": parts[2],
                "used_memory": parts[3],
                **metadata,
                "campaign_owned": bool(campaign_reasons),
                "campaign_ownership_reasons": campaign_reasons,
                "protected_owner_owned": bool(protected_reasons),
                "protected_owner_reasons": protected_reasons,
                "stale_nvidia_process": (
                    not metadata["metadata_available"]
                    and not metadata["process_present_after_metadata_read"]
                ),
            }
        )
    return {
        "time_utc": _utc_now(),
        "gpus": gpus,
        "compute_processes": processes,
    }


def _identity(process: dict[str, Any]) -> dict[str, Any]:
    return {field: process.get(field) for field in IDENTITY_FIELDS}


def _identity_key(process: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(process.get(field) for field in IDENTITY_FIELDS)


def _stale_key(process: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(process.get("gpu_uuid") or ""),
        int(process.get("pid") or 0),
        str(process.get("process_name") or ""),
    )


def establish_baseline(
    snapshot: dict[str, Any],
    policy: dict[str, Any],
    *,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    """Pin the current protected owner; refuse an empty or ambiguous baseline."""
    normalized = validate_policy(policy)
    protected_indices = set(normalized["protected_gpu_indices"])
    protected = [
        process
        for process in snapshot.get("compute_processes", [])
        if str(process.get("physical_index")) in protected_indices
    ]
    occupied = {str(process.get("physical_index")) for process in protected}
    if occupied != protected_indices:
        raise RuntimeError(
            "protected baseline must occupy every protected GPU exactly as a set; "
            f"expected {sorted(protected_indices)}, found {sorted(occupied)}"
        )
    invalid = [
        process
        for process in protected
        if not process.get("protected_owner_owned")
        or process.get("campaign_owned")
        or not process.get("metadata_available")
    ]
    if invalid:
        raise RuntimeError(f"refusing invalid protected baseline processes: {invalid}")
    identities = [_identity(process) for process in protected]
    if len({_identity_key(process) for process in identities}) != len(identities):
        raise RuntimeError("protected baseline contains duplicate identities")
    baseline = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "policy": normalized,
        "gpu_inventory": list(snapshot.get("gpus", [])),
        "protected_gpu_processes": identities,
    }
    baseline["baseline_sha256"] = _canonical_sha256(baseline)
    if baseline_path is not None:
        _atomic_write_json(baseline_path, baseline)
    return baseline


def _verify_baseline(baseline: dict[str, Any]) -> None:
    digest = str(baseline.get("baseline_sha256") or "")
    unsigned = dict(baseline)
    unsigned.pop("baseline_sha256", None)
    if not digest or digest != _canonical_sha256(unsigned):
        raise ValueError("baseline_sha256 is missing or does not match baseline content")


def evaluate_snapshot(
    snapshot: dict[str, Any],
    baseline: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None = None,
    now_epoch: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate one snapshot and return its receipt plus next recent-owner state."""
    _verify_baseline(baseline)
    policy = validate_policy(dict(baseline.get("policy") or {}))
    expected = baseline.get("protected_gpu_processes")
    if not isinstance(expected, list) or not expected:
        raise ValueError("protected_gpu_processes must be non-empty")
    now = time.time() if now_epoch is None else float(now_epoch)
    grace = float(policy["stale_process_grace_seconds"])
    protected_indices = set(policy["protected_gpu_indices"])
    training_indices = set(policy["training_gpu_indices"])
    processes = list(snapshot.get("compute_processes", []))
    protected = [
        process
        for process in processes
        if str(process.get("physical_index")) in protected_indices
    ]
    training = [
        process
        for process in processes
        if str(process.get("physical_index")) in training_indices
    ]

    expected_by_key = {_identity_key(process): process for process in expected}
    current_by_key = {_identity_key(process): process for process in protected}
    missing = [
        expected_by_key[key]
        for key in sorted(expected_by_key.keys() - current_by_key.keys(), key=str)
    ]
    unexpected = [
        current_by_key[key]
        for key in sorted(current_by_key.keys() - expected_by_key.keys(), key=str)
    ]
    campaign_on_protected = [
        process for process in protected if process.get("campaign_owned")
    ]

    recent: dict[tuple[str, int, str], dict[str, Any]] = {}
    previous_matches_baseline = (
        (previous_state or {}).get("baseline_sha256") == baseline["baseline_sha256"]
    )
    previous_records = (
        (previous_state or {}).get("recent_campaign_processes", [])
        if previous_matches_baseline
        else []
    )
    for record in previous_records:
        if not isinstance(record, dict):
            continue
        age = now - float(record.get("last_verified_at") or 0)
        if 0 <= age <= grace:
            recent[_stale_key(record)] = dict(record)

    allowed_stale: list[dict[str, Any]] = []
    unauthorized_training: list[dict[str, Any]] = []
    for process in training:
        if process.get("campaign_owned") and process.get("metadata_available"):
            recent[_stale_key(process)] = {
                "gpu_uuid": str(process.get("gpu_uuid") or ""),
                "physical_index": str(process.get("physical_index") or ""),
                "pid": int(process.get("pid") or 0),
                "process_name": str(process.get("process_name") or ""),
                "last_verified_at": now,
                "identity": _identity(process),
            }
            continue
        if process.get("stale_nvidia_process"):
            prior = recent.get(_stale_key(process))
            if prior is not None:
                authorized = dict(process)
                authorized["last_verified_at"] = prior["last_verified_at"]
                authorized["stale_age_seconds"] = now - float(
                    prior["last_verified_at"]
                )
                allowed_stale.append(authorized)
                continue
        unauthorized_training.append(process)

    next_recent = [
        record
        for record in recent.values()
        if 0 <= now - float(record.get("last_verified_at") or 0) <= grace
    ]
    next_recent.sort(
        key=lambda record: (
            record["gpu_uuid"],
            record["pid"],
            record["process_name"],
        )
    )
    next_state = {
        "schema_version": 1,
        "updated_at": now,
        "baseline_sha256": baseline["baseline_sha256"],
        "recent_campaign_processes": next_recent,
    }
    ok = not any(
        (
            missing,
            unexpected,
            campaign_on_protected,
            unauthorized_training,
        )
    )
    receipt = {
        "time_utc": _utc_now(),
        "time_epoch": now,
        "policy": policy,
        "baseline_sha256": baseline["baseline_sha256"],
        "previous_state_baseline_matched": previous_matches_baseline,
        "gpu_guard_ok": ok,
        "campaign_processes_on_protected_gpus": campaign_on_protected,
        "missing_protected_baseline_processes": missing,
        "unexpected_protected_gpu_processes": unexpected,
        "authorized_stale_training_processes": allowed_stale,
        "unauthorized_processes_on_training_gpus": unauthorized_training,
        "gpus": list(snapshot.get("gpus", [])),
        "compute_processes": processes,
    }
    return receipt, next_state


def check_gpu_ownership(
    baseline_path: Path,
    *,
    state_path: Path,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Capture, evaluate, and persist recent owner state for the next poll."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    snapshot = capture_gpu_snapshot(dict(baseline.get("policy") or {}))
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        with exclusive_file_lock(
            handle,
            lock_name=f"GPU ownership state lock {lock_path}",
        ):
            try:
                previous_state = json.loads(state_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                previous_state = {}
            receipt, next_state = evaluate_snapshot(
                snapshot,
                baseline,
                previous_state=previous_state,
            )
            _atomic_write_json(state_path, next_state)
    if receipt_path is not None:
        _atomic_write_json(receipt_path, receipt)
    return receipt


def _compact(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "time_utc": receipt["time_utc"],
        "gpu_guard_ok": receipt["gpu_guard_ok"],
        "baseline_sha256": receipt["baseline_sha256"],
        "campaign_processes_on_protected_gpus": len(
            receipt["campaign_processes_on_protected_gpus"]
        ),
        "missing_protected_baseline_processes": len(
            receipt["missing_protected_baseline_processes"]
        ),
        "unexpected_protected_gpu_processes": len(
            receipt["unexpected_protected_gpu_processes"]
        ),
        "authorized_stale_training_processes": len(
            receipt["authorized_stale_training_processes"]
        ),
        "unauthorized_processes_on_training_gpus": len(
            receipt["unauthorized_processes_on_training_gpus"]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("baseline", "check"))
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "baseline":
        if args.policy is None:
            parser.error("baseline requires --policy")
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        result = establish_baseline(
            capture_gpu_snapshot(policy),
            policy,
            baseline_path=args.baseline,
        )
        if args.receipt is not None:
            _atomic_write_json(args.receipt, result)
        print(json.dumps(result, sort_keys=True))
        return 0

    state_path = args.state or args.baseline.with_suffix(".state.json")
    result = check_gpu_ownership(
        args.baseline,
        state_path=state_path,
        receipt_path=args.receipt,
    )
    print(json.dumps(_compact(result) if args.compact else result, sort_keys=True))
    return 0 if result["gpu_guard_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IDENTITY_FIELDS",
    "capture_gpu_snapshot",
    "check_gpu_ownership",
    "establish_baseline",
    "evaluate_snapshot",
    "read_process_metadata",
    "validate_policy",
]
