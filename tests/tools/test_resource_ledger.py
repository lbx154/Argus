from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from argus_skill.engineer.external_work import ExternalWorkState, scan_external_work
from argus_skill.tools.resource_ledger.ledger import ResourceLedger, owner_identity
from argus_skill.tools.resource_ledger.probe import NvidiaAdapter, ResourceProbe
from argus_skill.tools.subagent import _cli as subagent_cli
from argus_skill.tools.subagent import _resource_admission
from argus_skill.tools.subagent._direct_run import _run_direct
from argus_skill.tools.subagent._registry import _read_task, _write_task


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


def _submit_args(**updates: object) -> argparse.Namespace:
    values = {
        "task_id": "submit-test",
        "description": "test",
        "command": "echo ok",
        "mode": "direct",
        "timeout": 10,
        "monitor_interval": 120,
        "model": None,
        "run_dir": None,
        "cwd": None,
        "override_discussion": None,
        "clear_stop": False,
        "no_preflight": False,
        "cpu_count": 0,
        "cpu_ids": None,
        "accelerator": None,
        "gpu_count": None,
        "gpu_mem_mib": None,
        "expected_duration": None,
        "checkpointable": None,
        "intent": None,
    }
    values.update(updates)
    return argparse.Namespace(**values)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="parent submit shape uses POSIX fork")
def test_no_demand_submit_keeps_legacy_record_and_receipt_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subagent_cli.os, "fork", lambda: 4242)
    assert subagent_cli.cmd_submit(_submit_args()) == 0
    receipt = json.loads(capsys.readouterr().out)
    record = _read_task("submit-test") or {}
    assert not any(key.startswith("resource_") for key in receipt)
    assert not any(key.startswith("resource_") for key in record)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="parent submit shape uses POSIX fork")
def test_declared_demand_is_persisted_for_worker_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subagent_cli.os, "fork", lambda: 4242)
    args = _submit_args(
        task_id="demand-test",
        accelerator="cuda",
        gpu_count=2,
        gpu_mem_mib=40,
        expected_duration=300,
        checkpointable=True,
        intent="two-card training",
    )
    assert subagent_cli.cmd_submit(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    record = _read_task("demand-test") or {}
    assert receipt["resource_demand"] == record["resource_demand"]
    assert record["resource_demand"]["device_count"] == 2


def test_direct_worker_exports_grant_env_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = ResourceLedger(tmp_path / "ledger", probe=lambda: _snapshot())
    monkeypatch.setattr(_resource_admission, "ResourceLedger", lambda: ledger)
    _write_task("env-task", {
        "state": "starting",
        "task_id": "env-task",
        "run_id": "env-run",
        "resource_demand": _demand("env test"),
    })
    _run_direct(
        "env-task",
        "printf '%s' \"$CUDA_VISIBLE_DEVICES\"",
        "env",
        timeout=10,
        cwd=str(tmp_path),
    )
    record = _read_task("env-task") or {}
    assert record["state"] == "done"
    assert record["stdout_tail"] == "0"
    assert record["resource_grant_id"]
    assert ledger.status(refresh_probe=False)["grants"] == []


def test_yield_request_round_trips_into_external_work_facts(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledger"
    ledger = ResourceLedger(ledger_root, probe=lambda: _snapshot())
    grant = ledger.acquire(_demand("training"), owner=_owner(tmp_path, "train"))
    request = ledger.yield_request(grant["id"], "checkpoint soon for an urgent eval")
    registry = tmp_path / ".argus_subagents"
    registry.mkdir()
    log = tmp_path / "live.log"
    log.write_text("running\n", encoding="utf-8")
    (registry / "train.json").write_text(json.dumps({
        "state": "running",
        "task_id": "train",
        "mode": "direct",
        "pid": os.getpid(),
        "worker_pid": os.getpid(),
        "stdout_log": str(log),
        "resource_grant_id": grant["id"],
        "resource_ledger_root": str(ledger_root),
    }), encoding="utf-8")

    status = scan_external_work(tmp_path)[0]
    assert status.state is ExternalWorkState.RUNNING_HEALTHY
    assert any("someone asks for the card" in fact for fact in status.facts)
    response = ledger.respond_yield(
        grant["id"], request["id"], "run is at an unsafe checkpoint boundary"
    )
    assert response["response"]["decision"] == "decline"


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
            sys.executable, "-m", "argus_skill.tools.resource_ledger", "run",
            "--accelerator", "none", "--ttl", "1", "--", "/bin/true",
        ],
        env=_run_env(root),
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert list((root / "grants").glob("*.json")) == []


@pytest.mark.skipif(os.name == "nt", reason="kill -9 and procfs are POSIX-specific")
def test_run_wrapper_kill9_leaves_only_ttl_bounded_grant(tmp_path: Path) -> None:
    root = tmp_path / "killed-ledger"
    child_pid_path = tmp_path / "child.pid"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "argus_skill.tools.resource_ledger", "run",
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
