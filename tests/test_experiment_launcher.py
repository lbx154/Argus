from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import benchmarks.experiment_launcher as experiment_launcher


def _make_network_record(
    name: str,
    *,
    created: datetime,
    containers: dict[str, Any] | None = None,
    project: str = "harbor-project",
) -> dict[str, Any]:
    return {
        "Name": name,
        "Driver": "bridge",
        "Created": created.isoformat().replace("+00:00", "Z"),
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.network": "default",
        },
        "Containers": containers or {},
    }


def _build_fake_docker_runner(
    records: dict[str, dict[str, Any]],
    *,
    removed: list[str],
):
    def _runner(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001
        capture_output: bool = False,  # noqa: ARG001
        text: bool = False,  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if command == ["docker", "network", "ls", "--format", "{{.ID}}"]:
            return subprocess.CompletedProcess(command, 0, stdout="\n".join(records))
        if command[:3] == ["docker", "network", "inspect"]:
            network_id = command[3]
            payload = [records[network_id]]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))
        if command[:3] == ["docker", "network", "rm"]:
            removed.append(command[3])
            return subprocess.CompletedProcess(command, 0, stdout=f"{command[3]}\n")
        raise AssertionError(f"unexpected docker command: {command}")

    return _runner


def _wait_for(path: Path, predicate, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
            if payload is not None and predicate(payload):
                return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {path}")


def test_detached_launcher_writes_bundle_and_status(tmp_path: Path) -> None:
    run_root = tmp_path / "experiments"
    cmd = [
        sys.executable,
        "-m",
        "benchmarks.experiment_launcher",
        "--run-root",
        str(run_root),
        "--run-id",
        "tb2-test-20260515T000000Z",
        "--metadata",
        "dataset_id=terminal-bench@2.0",
        "--metadata",
        "pricing_source=test-pricing",
        "--",
        sys.executable,
        "-c",
        "import sys; print('hello from detached launcher'); print('oops', file=sys.stderr)",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr

    run_dir = run_root / "tb2-test-20260515T000000Z"
    manifest_path = run_dir / "manifest.json"
    status_path = run_dir / "status.json"
    pid_path = run_dir / "pid"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    assert run_dir.exists()
    assert manifest_path.exists()
    assert status_path.exists()
    assert pid_path.exists()
    assert stdout_path.exists()
    assert stderr_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["command"][-2:] == ["-c", "import sys; print('hello from detached launcher'); print('oops', file=sys.stderr)"]
    assert manifest["metadata"]["dataset_id"] == "terminal-bench@2.0"
    assert manifest["metadata"]["pricing_source"] == "test-pricing"
    assert manifest["env_config_hash"]

    _wait_for(status_path, lambda payload: payload.get("state") in {"completed", "failed"})
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["exit_code"] == 0
    assert status["child_pid"] > 0
    assert pid_path.read_text(encoding="utf-8").strip() == str(status["child_pid"])
    assert "hello from detached launcher" in stdout_path.read_text(encoding="utf-8")
    assert "oops" in stderr_path.read_text(encoding="utf-8")


def test_cleanup_orphan_compose_networks_removes_only_stale_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    records = {
        "stale": _make_network_record(
            "stale_default",
            created=now - timedelta(hours=2),
        ),
        "active": _make_network_record(
            "active_default",
            created=now - timedelta(hours=2),
            containers={"abc": {"Name": "svc"}},
            project="active-project",
        ),
        "recent": _make_network_record(
            "recent_default",
            created=now - timedelta(minutes=5),
        ),
        "other": {
            "Name": "other",
            "Driver": "bridge",
            "Created": (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "Labels": {"com.docker.compose.network": "internal"},
            "Containers": {},
        },
    }
    removed: list[str] = []
    monkeypatch.setattr(
        experiment_launcher,
        "_run_docker_command",
        _build_fake_docker_runner(records, removed=removed),
    )

    result = experiment_launcher.cleanup_orphan_compose_networks()

    assert result == ["stale"]
    assert removed == ["stale"]


def test_launch_detached_invokes_compose_network_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[bool] = []

    def _fake_cleanup(*, max_age: Any = None) -> list[str]:
        calls.append(True)
        return []

    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, D401
            self.pid = 4321

        def __enter__(self):  # noqa: D401
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: D401
            return False

    monkeypatch.setattr(experiment_launcher, "cleanup_orphan_compose_networks", _fake_cleanup)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    spec = experiment_launcher.LaunchSpec(
        run_root=tmp_path / "experiments",
        run_id="tb2-test-cleanup",
        command=[sys.executable, "-c", "print('hi')"],
        cwd=tmp_path,
        env={},
        metadata={},
    )

    run_dir = experiment_launcher.launch_detached(spec)

    assert calls == [True]
    assert run_dir == spec.run_root / spec.run_id


def test_launch_detached_records_preflight_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def _preflight() -> dict[str, Any]:
        calls.append("preflight")
        return {
            "state": "launch_failed",
            "message": "docker pull failed for alexgshaw/cancel-async-tasks:20251031 (docker hub rate limit)",
            "exit_code": 1,
            "missing_image": "alexgshaw/cancel-async-tasks:20251031",
            "rate_limit": True,
        }

    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, D401
            raise AssertionError("worker should not start when preflight fails")

    monkeypatch.setattr(experiment_launcher, "cleanup_orphan_compose_networks", lambda: [])
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    spec = experiment_launcher.LaunchSpec(
        run_root=tmp_path / "experiments",
        run_id="tb2-test-preflight-failed",
        command=[sys.executable, "-c", "print('should not run')"],
        cwd=tmp_path,
        env={},
        metadata={},
        preflight=_preflight,
    )

    run_dir = experiment_launcher.launch_detached(spec)

    assert calls == ["preflight"]
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "launch_failed"
    assert status["message"].startswith("docker pull failed")
    assert status["exit_code"] == 1
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "preflight.json").exists()
    preflight = json.loads((run_dir / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["state"] == "launch_failed"
    assert (run_dir / "stderr.log").read_text(encoding="utf-8").strip()


def test_launch_detached_records_successful_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def _preflight() -> dict[str, Any]:
        calls.append("preflight")
        return {
            "state": "preflight_complete",
            "exit_code": 0,
            "checked_images": [{"image": "alexgshaw/cancel-async-tasks:20251031", "present": True}],
            "staged_images": ["alexgshaw/cancel-async-tasks:20251031"],
        }

    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, D401
            self.pid = 4243

    monkeypatch.setattr(experiment_launcher, "cleanup_orphan_compose_networks", lambda: [])
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    spec = experiment_launcher.LaunchSpec(
        run_root=tmp_path / "experiments",
        run_id="tb2-test-preflight-ok",
        command=[sys.executable, "-c", "print('should run')"],
        cwd=tmp_path,
        env={},
        metadata={},
        preflight=_preflight,
    )

    run_dir = experiment_launcher.launch_detached(spec)

    assert calls == ["preflight"]
    assert (run_dir / "preflight.json").exists()
    preflight = json.loads((run_dir / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["state"] == "preflight_complete"
    assert preflight["staged_images"] == ["alexgshaw/cancel-async-tasks:20251031"]
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "launching"
    assert (run_dir / "manifest.json").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "tb2-test-preflight-ok"


def test_worker_main_finalizes_failed_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "experiments" / "tb2-test-failed"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "tb2-test-failed",
                "cwd": str(tmp_path),
                "command": [sys.executable, "-c", "import sys; sys.exit(7)"],
                "env": {},
                "status_path": "status.json",
                "pid_path": "pid",
                "stdout_log": "stdout.log",
                "stderr_log": "stderr.log",
            }
        ),
        encoding="utf-8",
    )

    class _FakePopen:
        pid = 4242

        def __init__(self, *args, **kwargs):  # noqa: ANN001
            self.args = args
            self.kwargs = kwargs

        def wait(self) -> int:
            return 7

        def kill(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    exit_code = experiment_launcher._worker_main(manifest_path)

    assert exit_code == 7
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["exit_code"] == 7
    assert status["child_pid"] == 4242
    assert status["ended_at"]
