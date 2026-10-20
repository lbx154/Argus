from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from argus.tools.resource_ledger import cli as ledger_cli
from argus.tools.resource_ledger.ledger import ResourceLedger, owner_identity
from argus.tools.resource_ledger.probe import NvidiaAdapter, ResourceProbe


def _snapshot(*, status: str = "available", enforcement: str = "strict") -> dict:
    devices = [{
        "identity": "GPU-stable-0",
        "index": "0",
        "name": "fake",
        "total_memory_mib": 100,
        "used_memory_mib": 0,
        "utilization_percent": 0.0,
        "visibility": "0",
    }] if status == "available" else []
    return {
        "captured_at": time.time(),
        "enforcement": enforcement,
        "accelerators": [{
            "kind": "cuda",
            "status": status,
            "visibility_env": "CUDA_VISIBLE_DEVICES",
            "devices": devices,
            "detail": "fake telemetry failure" if status != "available" else "",
        }],
        "cpu_memory": {"status": "available", "visible_cpu_ids": [0]},
    }


def _demand(intent: str = "test") -> dict:
    return {
        "accelerator": "cuda",
        "device_count": 1,
        "mem_mib_estimate": 50,
        "expected_duration_seconds": 60,
        "checkpointable": True,
        "intent": intent,
    }


def _owner(root: Path, task: str) -> dict:
    return owner_identity(project_root=root, task_id=task)


def test_overlapping_acquire_never_double_grants_device(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    root.mkdir()
    barrier = threading.Barrier(2)

    def acquire(task: str) -> dict:
        ledger = ResourceLedger(root, probe=lambda: _snapshot())
        barrier.wait()
        return ledger.acquire(_demand(task), owner=_owner(tmp_path, task))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ("one", "two")))

    assert sorted(result["state"] for result in results) == ["granted", "queued"]
    identities = [
        identity
        for result in results
        if result["state"] == "granted"
        for identity in result["grant"]["device_identities"]
    ]
    assert identities == ["GPU-stable-0"]


def test_queued_request_is_promoted_after_release(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path, probe=lambda: _snapshot())
    first = ledger.acquire(_demand("holder"), owner=_owner(tmp_path, "holder"))
    second = ledger.acquire(_demand("waiter"), owner=_owner(tmp_path, "waiter"))
    assert second["state"] == "queued"
    assert second["position"] == 1

    assert ledger.release(first["id"])
    promoted = ledger.acquire(
        _demand("waiter"),
        owner=_owner(tmp_path, "waiter"),
        request_id=second["id"],
    )
    assert promoted["state"] == "granted"
    assert promoted["grant"]["device_identities"] == ["GPU-stable-0"]


def test_expire_reclaims_vanished_owner_identity(tmp_path: Path) -> None:
    alive = {"value": True}
    ledger = ResourceLedger(
        tmp_path,
        probe=lambda: _snapshot(),
        identity_alive=lambda _owner: alive["value"],
    )
    grant = ledger.acquire(_demand(), owner=_owner(tmp_path, "gone"), ttl_seconds=5)
    alive["value"] = False
    assert ledger.expire() == [grant["id"]]
    assert ledger.status(refresh_probe=False)["grants"] == []


def test_inaccessible_probe_is_advisory_and_never_free(tmp_path: Path) -> None:
    ledger = ResourceLedger(
        tmp_path,
        probe=lambda: _snapshot(status="inaccessible", enforcement="advisory"),
    )
    first = ledger.acquire(_demand("one"), owner=_owner(tmp_path, "one"))
    second = ledger.acquire(_demand("two"), owner=_owner(tmp_path, "two"))
    assert first["state"] == second["state"] == "granted"
    assert first["enforcement"] == "advisory"
    assert first["grant"]["device_identities"] == []
    assert first["grant"]["env"] == {}
    assert "claims no free capacity" in first["warning"]


def test_nvidia_command_failure_is_inaccessible() -> None:
    def failed(_command: list[str]) -> str:
        raise subprocess.CalledProcessError(9, "nvidia-smi")

    adapter = NvidiaAdapter(run_command=failed, which=lambda _name: "/fake/nvidia-smi")
    result = ResourceProbe(adapters=[adapter]).snapshot()
    assert result["enforcement"] == "advisory"
    assert result["accelerators"][0]["status"] == "inaccessible"
    assert result["accelerators"][0]["devices"] == []


def test_admission_requires_matching_demand_host_and_task_identity(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path, probe=lambda: _snapshot())
    owner = _owner(tmp_path, "task")
    grant = ledger.acquire(_demand(), owner=owner)
    assert ledger.admit(grant["id"], demand=_demand(), owner=owner)
    changed = _demand()
    changed["mem_mib_estimate"] = 51
    assert ledger.admit(grant["id"], demand=changed, owner=owner) is None
    wrong_owner = dict(owner, task_id="other")
    assert ledger.admit(grant["id"], demand=_demand(), owner=wrong_owner) is None


def _run_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ARGUS_RESOURCE_LEDGER_DIR"] = str(root)
    env["PATH"] = ""
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return env


def test_run_wrapper_releases_on_normal_exit(tmp_path: Path) -> None:
    root = tmp_path / "normal-ledger"
    result = subprocess.run(
        [
            sys.executable, "-m", "argus.tools.resource_ledger", "run",
            "--accelerator", "none", "--ttl", "1", "--",
            sys.executable, "-c", "pass",
        ],
        env=_run_env(root),
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert list((root / "grants").glob("*.json")) == []


def test_run_without_max_wait_keeps_waiting_until_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class _Ledger:
        def __init__(self) -> None:
            self.calls = 0

        def acquire(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "id": "request-1",
                "state": "queued" if self.calls == 1 else "granted",
                "poll_after_seconds": 1,
            }

        def admit(self, *_args, **_kwargs):
            return {"grant": {"env": {}}}

        def release(self, *_args, **_kwargs):
            return True

    class _Proc:
        def wait(self, timeout=None):
            return 0

    ledger = _Ledger()
    monkeypatch.setattr(ledger_cli, "ResourceLedger", lambda: ledger)
    monkeypatch.setattr(ledger_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        ledger_cli.time,
        "sleep",
        lambda _delay: clock.__setitem__(0, clock[0] + 4001),
    )
    monkeypatch.setattr(ledger_cli.subprocess, "Popen", lambda *args, **kwargs: _Proc())
    args = ledger_cli.build_parser().parse_args(
        ["run", "--accelerator", "none", "--", "/bin/true"]
    )

    assert args.max_wait is None
    assert args.handler(args) == 0
    assert clock[0] > 3600
    assert ledger.calls == 2


@pytest.mark.skipif(os.name == "nt", reason="kill -9 and procfs are POSIX-specific")
def test_run_wrapper_kill9_leaves_only_ttl_bounded_grant(tmp_path: Path) -> None:
    root = tmp_path / "killed-ledger"
    child_pid_path = tmp_path / "child.pid"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "argus.tools.resource_ledger", "run",
            "--accelerator", "none", "--ttl", "0.3", "--",
            "/bin/sh", "-c", f"echo $$ > {child_pid_path}; exec /bin/sleep 30",
        ],
        env=_run_env(root),
    )
    child_pid = 0
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            grants = list((root / "grants").glob("*.json")) if root.exists() else []
            if grants and child_pid_path.exists():
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.05)
        assert grants
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        time.sleep(0.35)
        ledger = ResourceLedger(root, probe=lambda: _snapshot())
        assert ledger.expire()
        assert ledger.status(refresh_probe=False)["grants"] == []
    finally:
        if proc.poll() is None:
            proc.kill()
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_absent_hardware_is_unsatisfiable_not_queued(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    root.mkdir()
    ledger = ResourceLedger(root, probe=lambda: _snapshot(status="absent"))
    result = ledger.acquire(_demand("no-gpu-box"), owner=_owner(tmp_path, "no-gpu-box"))
    assert result["state"] == "unsatisfiable"
    assert "no cuda hardware" in result["unsatisfiable_reason"]
    assert list((root / "queue").glob("*.json")) == []


def test_request_without_accelerator_is_not_blocked_behind_waiting_gpu_request(
    tmp_path: Path,
) -> None:
    ledger = ResourceLedger(tmp_path, probe=lambda: _snapshot())
    ledger.acquire(_demand("holder"), owner=_owner(tmp_path, "holder"))
    waiting_gpu = ledger.acquire(_demand("gpu-waiter"), owner=_owner(tmp_path, "gpu-waiter"))
    assert waiting_gpu["state"] == "queued"

    cpu_only = dict(_demand("cpu-only"), accelerator="none", device_count=0, mem_mib_estimate=0)
    result = ledger.acquire(cpu_only, owner=_owner(tmp_path, "cpu-only"))

    assert result["state"] == "granted"
    still_waiting = ledger.acquire(
        _demand("gpu-waiter"), owner=_owner(tmp_path, "gpu-waiter"), request_id=waiting_gpu["id"],
    )
    assert still_waiting["state"] == "queued"


def test_same_kind_requests_keep_strict_order_behind_a_waiting_head(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["accelerators"][0]["devices"].append(dict(
        snapshot["accelerators"][0]["devices"][0], identity="GPU-stable-1", index="1", visibility="1",
    ))
    ledger = ResourceLedger(tmp_path, probe=lambda: snapshot)
    ledger.acquire(_demand("holder"), owner=_owner(tmp_path, "holder"))
    head = ledger.acquire(dict(_demand("two-cards"), device_count=2), owner=_owner(tmp_path, "head"))
    assert head["state"] == "queued"

    later = ledger.acquire(_demand("one-card"), owner=_owner(tmp_path, "later"))

    assert later["state"] == "queued"


def test_skipped_any_waiter_still_holds_back_later_specific_kind(tmp_path: Path) -> None:
    snapshot = _snapshot()
    rocm = dict(snapshot["accelerators"][0], kind="rocm", visibility_env="HIP_VISIBLE_DEVICES")
    rocm["devices"] = [dict(rocm["devices"][0], identity="ROCM-0")]
    snapshot["accelerators"].append(rocm)
    ledger = ResourceLedger(tmp_path, probe=lambda: snapshot)
    ledger.acquire(_demand("holder"), owner=_owner(tmp_path, "holder"))
    cuda_waiter = ledger.acquire(_demand("cuda-waiter"), owner=_owner(tmp_path, "cuda-waiter"))
    assert cuda_waiter["state"] == "queued"
    any_waiter = ledger.acquire(dict(_demand("any"), accelerator="any"), owner=_owner(tmp_path, "any"))
    assert any_waiter["state"] == "queued"

    later_rocm = ledger.acquire(dict(_demand("rocm"), accelerator="rocm"), owner=_owner(tmp_path, "rocm"))

    # The rocm card is one the earlier any-kind waiter could use, so the later
    # request must not jump ahead of it.
    assert later_rocm["state"] == "queued"


def test_more_cards_or_memory_than_the_host_has_is_unsatisfiable(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    root.mkdir()
    ledger = ResourceLedger(root, probe=lambda: _snapshot())

    two_cards = ledger.acquire(dict(_demand("two"), device_count=2), owner=_owner(tmp_path, "two"))
    big_card = ledger.acquire(
        dict(_demand("big"), mem_mib_estimate=200), owner=_owner(tmp_path, "big"),
    )

    assert two_cards["state"] == big_card["state"] == "unsatisfiable"
    assert "2 device(s)" in two_cards["unsatisfiable_reason"]
    assert "200 MiB" in big_card["unsatisfiable_reason"]
    assert list((root / "queue").glob("*.json")) == []


def test_unknown_card_memory_never_makes_a_demand_unsatisfiable(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["accelerators"][0]["devices"][0]["total_memory_mib"] = 0
    ledger = ResourceLedger(tmp_path / "ledger", probe=lambda: snapshot)

    result = ledger.acquire(dict(_demand("big"), mem_mib_estimate=200), owner=_owner(tmp_path, "big"))

    assert result["state"] != "unsatisfiable"

