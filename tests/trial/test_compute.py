from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus_skill.trial import compute_client
from argus_skill.trial.compute import (
    GIB,
    GPU_SECONDS,
    LABEL,
    ComputeService,
    DockerExecutor,
    InfrastructureError,
    create_app,
    load_config,
    main,
)
from argus_skill.trial.store import Store, TrialError


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class FakeDocker:
    def __init__(self, clock):
        self.clock = clock
        self.items = {}
        self.launched = []
        self.killed = []
        self.busy = set()
        self.memory = 995 * GIB
        self.storage = 1200 * GIB
        self.launch_error = False
        self.inventory_error = False
        self.gpu_error = False

    def containers(self, owner):
        if self.inventory_error:
            raise InfrastructureError("Docker inventory unavailable")
        return list(self.items.values())

    def busy_gpus(self, devices):
        if self.gpu_error:
            raise InfrastructureError("GPU probe failed")
        return self.busy

    def available_memory(self):
        return self.memory

    def available_storage(self, state_dir):
        return self.storage

    def launch(self, argv):
        self.launched.append(argv)
        if self.launch_error:
            raise InfrastructureError("Ambiguous Docker launch")
        labels = dict(argv[i + 1].split("=", 1) for i, arg in enumerate(argv) if arg == "--label")
        job_id = int(labels[LABEL + ".job"])
        cid = f"{job_id:064x}"
        self.items[job_id] = {
            "Id": cid, "Name": "/" + argv[argv.index("--name") + 1],
            "Config": {"Labels": labels},
            "State": {"Running": True, "Status": "running", "StartedAt": iso(self.clock()),
                      "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": 0},
        }
        return cid

    def finish(self, job_id, code=0):
        self.items[job_id]["State"].update(
            Running=False, Status="exited", FinishedAt=iso(self.clock()), ExitCode=code)

    def kill(self, cid):
        self.killed.append(cid)
        self.finish(int(cid, 16), 137)

    def logs(self, cid):
        return "output\nerror\n", False


@pytest.fixture
def environment(tmp_path):
    config = {
        "state_dir": str(tmp_path / "compute"),
        "trial_db": str(tmp_path / "usage.sqlite3"),
        "tenants": {},
        "image": "argus-web-compute:20260911", "uid": 583754321, "gid": 100,
        "cpu_limit": 120, "memory_gib": 900, "gpu_devices": [0, 1, 2, 3],
        "gpu_hours_per_tenant": 200,
    }
    store = Store(tmp_path / "usage.sqlite3")
    for tenant in ("trial-01", "trial-02", "trial-03"):
        root = tmp_path / tenant / "data"
        (root / "workspace" / "project").mkdir(parents=True)
        (root / "home").mkdir()
        config["tenants"][tenant] = {"data_dir": str(root)}
        store.issue(tenant, "key-" + tenant)
    now = [1000.0]
    docker = FakeDocker(lambda: now[0])
    return config, now, docker


@pytest.fixture
def service(environment):
    config, now, docker = environment
    svc = ComputeService(config, executor=docker, clock=lambda: now[0])
    svc.open()
    yield svc
    svc.close()


def submit(service, tenant="trial-01", **resources):
    return service.submit(tenant, {"command": ["python", "train.py"], **resources})


def test_interactive_memory_growth_is_reserved_under_parent_limit(environment):
    config, now, docker = environment
    config["interactive_memory_gib"] = 320
    docker.memory = 780 * GIB
    service = ComputeService(config, executor=docker, clock=lambda: now[0])
    service.open()
    try:
        with pytest.raises(TrialError, match="1..580") as rejected:
            submit(service, memory_gib=710, gpus=0)
        assert rejected.value.status == 400 and not docker.launched
        first = submit(service, memory_gib=580, gpus=0)
        docker.memory = 600 * GIB
        service.tick()
        assert not docker.launched
        docker.memory = 780 * GIB
        second = submit(service, "trial-02", memory_gib=1, gpus=0)
        service.tick()
        assert service.get("trial-01", first["id"])["status"] == "running"
        assert service.get("trial-02", second["id"])["status"] == "queued"
        capacity = service.status("trial-01")["capacity"]
        assert capacity["memory_gib"] == 580
        assert capacity["shared_memory_gib"] == 900
        assert capacity["interactive_memory_gib"] == 320
        assert capacity["cpus"] == 120 and capacity["gpus"] == 4
        assert capacity["memory_gib_allocated"] + capacity["interactive_memory_gib"] <= 900
    finally:
        service.close()


def test_lower_pool_policy_fails_incompatible_queued_work_explicitly(service, environment):
    _, _, docker = environment
    job = submit(service, memory_gib=710)
    service.config["interactive_memory_gib"] = 320
    docker.gpu_error = True
    service.tick()
    result = service.get("trial-01", job["id"])
    assert result["status"] == "failed"
    assert result["gpu_seconds_charged"] == 0
    assert "current compute pool" in result["error"]
    assert not docker.launched


def test_http_auth_idor_logs_and_validation(environment):
    config, now, docker = environment
    app = create_app(config, executor=docker, clock=lambda: now[0], poll_interval=None)
    auth = {"Authorization": "Bearer key-trial-01"}
    other = {"Authorization": "Bearer key-trial-02"}
    readonly = {**auth, "X-Argus-Readonly": "true"}
    with TestClient(app) as client:
        assert client.get("/compute/status").status_code == 401
        assert client.get("/compute/status", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/compute/status", headers=readonly).status_code == 200
        rejected = client.post("/compute/jobs", json={"command": ["echo", "hi"]}, headers=readonly)
        assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "readonly_session"
        assert client.get("/compute/jobs", headers=auth).json()["jobs"] == []
        response = client.post("/compute/jobs", json={"command": ["echo", "hi"]}, headers=auth)
        assert response.status_code == 202
        job_id = response.json()["job"]["id"]
        assert response.json()["job"]["status"] == "queued"
        assert client.get(f"/compute/jobs/{job_id}", headers=readonly).status_code == 200
        assert client.post(f"/compute/jobs/{job_id}/cancel", headers=readonly).status_code == 403
        assert client.get(f"/compute/jobs/{job_id}", headers=auth).json()["job"]["status"] == "queued"
        for suffix in ("", "/logs", "/cancel"):
            response = client.request("POST" if suffix == "/cancel" else "GET",
                                      f"/compute/jobs/{job_id}{suffix}", headers=other)
            assert response.status_code == 404
        assert client.get("/compute/jobs", headers=other).json() == {"jobs": []}
        assert client.get("/compute/jobs/999", headers=auth).status_code == 404
        for payload in ({"command": ["echo"], "cpus": True},
                        {"command": ["echo"], "cpus": 121},
                        {"command": ["echo"], "memory_gib": 901},
                        {"command": ["echo"], "gpus": 5},
                        {"command": ["echo"], "timeout_seconds": 86401},
                        {"command": ["echo"], "shm_gib": 17},
                        {"command": ["echo"], "tenant_id": "trial-02"},
                        {"command": ["echo"], "workdir": "../home"},
                        {"command": ["echo"], "workdir": "/etc"},
                        {"command": ["echo"], "workdir": "no-such-directory"},
                        {"command": ["x"] * 129}, {"command": ["x" * 32769]},
                        {"command": ["echo", "\0"]}, {"command": []}):
            assert client.post("/compute/jobs", json=payload, headers=auth).status_code == 400
        for payload in ({"command": ["echo", "\ud800"]}, {"command": ["echo"], "name": "\ud800"}):
            assert client.post("/compute/jobs", content=json.dumps(payload), headers=auth).status_code == 400
        assert client.post("/compute/jobs", content="x" * 65537, headers=auth).status_code == 413
        assert client.post("/compute/jobs", content="{", headers=auth).status_code == 400
        app.state.compute.tick()
        logs = client.get(f"/compute/jobs/{job_id}/logs", headers=auth).json()
        assert logs["logs"] == logs["text"] == "output\nerror\n"
        status = client.get("/compute/status", headers=auth).json()
        assert status["gpu_seconds_limit"] == 720000 and status["gpu_seconds_reserved"] == 3600
        assert "trial-02" not in json.dumps(status)
        assert str(config["state_dir"]) not in json.dumps(status)
        response = client.post(f"/compute/jobs/{job_id}/cancel", headers=auth)
        assert response.status_code == 202 and response.json()["job"]["status"] == "cancelling"
        app.state.compute.tick()
        assert client.get(f"/compute/jobs/{job_id}", headers=auth).json()["job"]["status"] == "cancelled"


def test_exact_quota_atomic_reservation_settlement_and_model_independence(service, environment):
    _, now, docker = environment
    # Two full-day, four-GPU reservations use 192 of the 200 GPU-hours.
    submit(service, gpus=4, timeout_seconds=86400)
    submit(service, gpus=4, timeout_seconds=86400)
    submit(service, gpus=4, timeout_seconds=7200)
    assert service.status("trial-01")["gpu_seconds_remaining"] == 0
    with pytest.raises(TrialError, match="Insufficient"):
        submit(service, timeout_seconds=1)
    assert service.auth.status("trial-01")["tokens_used"] == 0
    service.tick()
    now[0] += 12.25
    docker.finish(1)
    service.tick()
    assert service.get("trial-01", 1)["gpu_seconds_charged"] == 49
    service.tick()
    assert service.get("trial-01", 1)["gpu_seconds_charged"] == 49
    service.cancel("trial-01", 3)
    assert service.get("trial-01", 3)["gpu_seconds_charged"] == 0
    assert service.status("trial-01")["gpu_seconds_remaining"] == GPU_SECONDS - 345600 - 49

    def reserve(_):
        try:
            submit(service, "trial-02", gpus=4, timeout_seconds=86400)
            return True
        except TrialError:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(reserve, range(5))) == 2


def test_cpu_only_queue_bound_is_atomic_and_returns_explicit_http_error(environment):
    config, now, docker = environment
    app = create_app(config, executor=docker, clock=lambda: now[0], poll_interval=None)
    auth = {"Authorization": "Bearer key-trial-01"}
    payload = {"command": ["python", "train.py"], "gpus": 0, "timeout_seconds": 86400}
    with TestClient(app) as client:
        service = app.state.compute
        for _ in range(99):
            service.submit("trial-01", payload)

        def admit(_):
            try:
                service.submit("trial-01", payload)
                return 202
            except TrialError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(admit, range(5)))
        assert results.count(202) == 1 and results.count(429) == 4
        service.tick()
        assert service.status("trial-01")["job_counts"] == {"queued": 99, "running": 1}
        rejected = client.post("/compute/jobs", headers=auth, json=payload)
        assert rejected.status_code == 429 and rejected.json()["error"]["code"] == "queue_full"
        assert len(client.get("/compute/jobs", headers=auth).json()["jobs"]) == 100
        assert client.get("/compute/status", headers=auth).json()["gpu_seconds_remaining"] == GPU_SECONDS
        assert client.post("/compute/jobs/2/cancel", headers=auth).status_code == 202
        admitted = client.post("/compute/jobs", headers=auth, json=payload)
        assert admitted.status_code == 202 and admitted.json()["job"]["id"] == 101
        assert client.post("/compute/jobs", headers={"Authorization": "Bearer key-trial-02"},
                           json=payload).status_code == 202


def test_fairness_full_pool_borrowing_concurrency_and_cpu_only(service, environment):
    _, now, docker = environment
    first = submit(service, cpus=120, memory_gib=900, gpus=4)
    next_same = submit(service, gpus=0)
    other = submit(service, "trial-02", gpus=0)
    service.tick()
    assert service.get("trial-01", first["id"])["status"] == "running"
    assert service.get("trial-02", other["id"])["status"] == "queued"
    now[0] += 10
    docker.finish(first["id"])
    service.tick()
    assert len(docker.launched) == 3
    assert "io.argus.compute.tenant=trial-02" in docker.launched[1]
    assert "io.argus.compute.tenant=trial-01" in docker.launched[2]
    assert "--gpus" not in docker.launched[1]
    assert "--runtime" not in docker.launched[1]
    assert "NVIDIA_VISIBLE_DEVICES=void" in docker.launched[1]
    assert service.get("trial-01", next_same["id"])["gpu_seconds_reserved"] == 0
    counts = service.status("trial-01")["capacity"]
    assert counts["cpus_allocated"] == 16 and counts["memory_gib_allocated"] == 64
    assert len(docker.killed) == 0
    another = submit(service, gpus=0)
    service.tick()
    assert service.get("trial-01", another["id"])["status"] == "queued"
    assert len(docker.launched) == 3  # One active job/account even when capacity is free.


def test_busy_external_gpus_memory_reserve_and_probe_failure(service, environment):
    _, _, docker = environment
    docker.busy = {0, 2, 3}
    gpu = submit(service)
    cpu = submit(service, "trial-02", gpus=0)
    docker.memory = 95 * GIB
    service.tick()
    assert not docker.launched  # 32 GiB cannot leave the required 64 GiB.
    docker.memory = 128 * GIB
    service.tick()
    assert '"device=1"' in docker.launched[0]
    assert service.get("trial-02", cpu["id"])["status"] == "running"
    third = submit(service, "trial-03", gpus=0)
    docker.memory = 100 * GIB
    service.tick()
    assert service.get("trial-03", third["id"])["status"] == "queued"
    service.cancel("trial-03", third["id"])
    docker.memory = 128 * GIB
    docker.finish(gpu["id"])
    docker.finish(cpu["id"])
    service.tick()
    submit(service)
    submit(service, "trial-02", gpus=0)
    docker.gpu_error = True
    with pytest.raises(InfrastructureError, match="GPU probe"):
        service.tick()
    assert len(docker.launched) == 3  # CPU work still runs without GPU evidence.
    assert service.status("trial-01")["job_counts"]["queued"] == 1


def test_cancel_timeout_and_no_double_charge(service, environment):
    _, now, docker = environment
    queued = submit(service, gpus=2)
    service.cancel("trial-01", queued["id"])
    assert service.get("trial-01", queued["id"])["gpu_seconds_charged"] == 0
    running = submit(service, gpus=2)
    timeout = submit(service, "trial-02", timeout_seconds=10)
    service.tick()
    now[0] += 5
    service.cancel("trial-01", running["id"])
    service.tick()
    assert service.get("trial-01", running["id"])["status"] == "cancelled"
    assert service.get("trial-01", running["id"])["gpu_seconds_charged"] == 10
    now[0] += 5
    service.tick()
    assert service.get("trial-02", timeout["id"])["status"] == "timed_out"
    assert service.get("trial-02", timeout["id"])["gpu_seconds_charged"] == 10
    service.tick()
    assert docker.killed == [f"{running['id']:064x}", f"{timeout['id']:064x}"]
    assert len(docker.launched) == 2


def test_restart_reconciles_completed_and_running_evidence_and_exclusive_lock(service, environment):
    config, now, docker = environment
    first = submit(service)
    second = submit(service, "trial-02")
    queued = submit(service, "trial-02")
    service.tick()
    duplicate = ComputeService(config, executor=docker)
    with pytest.raises(RuntimeError, match="Another"):
        duplicate.open()
    now[0] += 17
    docker.finish(first["id"])
    service.close()
    replacement = ComputeService(config, executor=docker, clock=lambda: now[0])
    replacement.open()
    try:
        replacement.tick()
        assert replacement.get("trial-01", first["id"])["status"] == "succeeded"
        assert replacement.get("trial-01", first["id"])["gpu_seconds_charged"] == 17
        assert replacement.get("trial-02", second["id"])["status"] == "running"
        assert replacement.get("trial-02", queued["id"])["status"] == "queued"
        replacement.tick()
        assert len(docker.launched) == 2
    finally:
        replacement.close()


def test_ambiguous_launch_missing_evidence_never_replayed_or_refunded(service, environment):
    _, _, docker = environment
    job = submit(service)
    docker.launch_error = True
    with pytest.raises(InfrastructureError):
        service.tick()
    assert service.get("trial-01", job["id"])["status"] == "starting"
    service.close()
    service.open()
    service.tick()
    assert service.get("trial-01", job["id"])["status"] == "blocked"
    assert service.status("trial-01")["gpu_seconds_reserved"] == 3600
    service.cancel("trial-01", job["id"])
    service.tick()
    assert not docker.killed and len(docker.launched) == 1
    # A timed-out CLI can leave a container which becomes visible later.
    docker.launch_error = False
    argv = docker.launched[0]
    docker.launch(argv)
    docker.launched.pop()
    service.tick()
    assert service.get("trial-01", job["id"])["status"] == "cancelled"
    assert len(docker.launched) == 1 and len(docker.killed) == 1


def test_created_state_and_foreign_identity_fail_closed(service, environment):
    _, _, docker = environment
    job = submit(service)
    service.tick()
    evidence = docker.items[job["id"]]
    evidence["State"].update(Running=False, Status="created", StartedAt="0001-01-01T00:00:00Z")
    service.tick()
    assert service.get("trial-01", job["id"])["status"] == "blocked"
    assert service.get("trial-01", job["id"])["gpu_seconds_charged"] == 3600
    evidence["Config"]["Labels"][LABEL + ".tenant"] = "trial-02"
    service.cancel("trial-01", job["id"])
    with pytest.raises(InfrastructureError, match="identity"):
        service.tick()
    assert not docker.killed


def test_secure_docker_arguments_and_path_boundaries(service, environment):
    config, _, docker = environment
    job = submit(service, command=["bash", "-c", "echo hello; cat /etc/passwd"], gpus=2, workdir="project")
    service.tick()
    argv = docker.launched[0]
    for flag, expected in (
        ("--user", "583754321:100"), ("--network", "none"), ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"), ("--pids-limit", "-1"),
        ("--memory", "32g"), ("--memory-swap", "32g"),
        ("--shm-size", "1g"), ("--gpus", '"device=0,1"'),
        ("--runtime", "nvidia"), ("--workdir", "/tenant/workspace/project"),
    ):
        assert argv[argv.index(flag) + 1] == expected
    assert "--read-only" in argv and "--privileged" not in argv and "--rm" not in argv
    assert argv.count("--mount") == 1
    assert "max-size=10m" in argv and "max-file=3" in argv
    assert "--entrypoint" not in argv
    assert "--cgroup-parent" not in argv
    assert argv[-4:] == [config["image"], "bash", "-c", "echo hello; cat /etc/passwd"]
    assert "HTTPS_PROXY=" in argv and "HTTPS_PROXY=http://127.0.0.1:3128" not in argv
    assert "docker.sock" not in " ".join(argv) and "usage.sqlite3" not in " ".join(argv)
    assert "key-trial" not in " ".join(argv)
    root = config["tenants"]["trial-01"]["data_dir"]
    (root / "workspace" / "escape").symlink_to(config["tenants"]["trial-02"]["data_dir"])
    with pytest.raises(TrialError, match="beneath workspace"):
        submit(service, workdir="escape")
    job2 = submit(service, workdir="project")
    (root / "workspace" / "project").rmdir()
    (root / "workspace" / "project").symlink_to(root / "home")
    docker.finish(job["id"])
    service.tick()
    assert service.get("trial-01", job2["id"])["status"] == "failed"
    assert service.get("trial-01", job2["id"])["gpu_seconds_charged"] == 0
    assert len(docker.launched) == 1


def test_optional_egress_mount_preserves_entrypoint_and_network_isolation(environment, tmp_path):
    config, now, docker = environment
    egress = tmp_path / "egress"
    egress.mkdir()
    config["egress_socket_dir"] = str(egress)
    config["cgroup_parent"] = "argus-trial.slice"
    service = ComputeService(config, executor=docker, clock=lambda: now[0])
    service.open()
    try:
        submit(service, command=["python", "download.py"], gpus=1)
        service.tick()
        argv = docker.launched[0]
        mounts = [argv[i + 1] for i, value in enumerate(argv) if value == "--mount"]
        assert len(mounts) == 2 and f"type=bind,src={egress},dst=/egress,readonly" in mounts
        assert argv[argv.index("--network") + 1] == "none"
        assert argv[argv.index("--cgroup-parent") + 1] == "argus-trial.slice"
        assert "--entrypoint" not in argv and argv[-3:] == [config["image"], "python", "download.py"]
        assert "HTTPS_PROXY=http://127.0.0.1:3128" in argv
        assert "HTTP_PROXY=http://127.0.0.1:3128" in argv
        assert "NO_PROXY=localhost,127.0.0.1,::1" in argv
        assert all("/compute" not in mount and "/meter" not in mount for mount in mounts)
        assert "api_key" not in " ".join(argv) and "key-trial" not in " ".join(argv)
        for invalid in (str(tmp_path), str(config["tenants"]["trial-02"]["data_dir"]), "relative/path"):
            with pytest.raises(ValueError):
                load_config({**config, "egress_socket_dir": invalid})
    finally:
        service.close()


def test_production_tenant_volume_boot_and_dispatch_guards(environment, monkeypatch):
    config, _, _ = environment
    executor = DockerExecutor()
    service = ComputeService(config, executor=executor)
    with pytest.raises(InfrastructureError, match="not mounted"):
        service.open()
    assert service.lock_file is None
    monkeypatch.setattr(Path, "is_mount", lambda path: True)
    for tenant, options in config["tenants"].items():
        (options["data_dir"] / ".tenant-volume").write_text(tenant + "\n")
    service.open()
    try:
        root = config["tenants"]["trial-01"]["data_dir"]
        marker = root / ".tenant-volume"
        marker.write_text("trial-02\n")
        with pytest.raises(InfrastructureError, match="does not match"):
            submit(service)
        marker.unlink()
        marker.symlink_to(root / "home" / "not-a-marker")
        with pytest.raises(InfrastructureError, match="unavailable"):
            submit(service)
        marker.unlink()
        marker.write_text("trial-01\n")
        job = submit(service)
        monkeypatch.setattr(Path, "is_mount", lambda path: False)
        with service.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        with pytest.raises(InfrastructureError, match="not mounted"):
            service.docker_command(row)
        assert service.get("trial-01", job["id"])["status"] == "queued"
        with pytest.raises(ValueError, match="cgroup_parent"):
            load_config({**config, "cgroup_parent": "../another.slice"})
    finally:
        service.close()


def test_physical_storage_reserve_retains_queue_and_resumes(environment):
    config, now, docker = environment
    service = ComputeService(config, executor=docker, clock=lambda: now[0])
    service.open()
    job = submit(service)
    service.close()
    docker.storage = 64 * GIB - 1
    app = create_app(config, executor=docker, clock=lambda: now[0], poll_interval=None)
    auth = {"Authorization": "Bearer key-trial-01"}
    with TestClient(app) as client:
        status = client.get("/compute/status", headers=auth).json()
        assert "physical storage below 64 GiB" in status["scheduler_error"]
        assert status["gpu_seconds_reserved"] == 3600
        assert client.get(f"/compute/jobs/{job['id']}", headers=auth).json()["job"]["status"] == "queued"
        assert not docker.launched
        docker.storage = 64 * GIB
        app.state.compute.tick()
        assert len(docker.launched) == 1
        assert client.get("/compute/status", headers=auth).json()["scheduler_error"] is None


def test_separate_service_socket_directory_and_storage_probe_errors(environment, tmp_path, monkeypatch):
    config, _, _ = environment
    config_file = tmp_path / "compute.json"
    config_file.write_text(json.dumps(config))
    socket = tmp_path / "compute-socket" / "compute.sock"
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    main(["--config", str(config_file), "--uds", str(socket)])
    assert calls == [{"uds": str(socket), "access_log": False, "proxy_headers": False}]
    with pytest.raises(InfrastructureError, match="storage probe failed"):
        DockerExecutor().available_storage(tmp_path / "not-a-filesystem-path")


def test_production_gpu_probe_is_explicit_and_uses_configured_devices():
    executor = DockerExecutor()
    outputs = iter([
        "0, GPU-a, 1, 0, Disabled\n1, GPU-b, 2, 0, Disabled\n"
        "2, GPU-c, 1, 0, Disabled\n3, GPU-d, 1, 0, Enabled\n",
        "GPU-c\n",
    ])
    executor.command = lambda argv: next(outputs)
    assert executor.busy_gpus([0, 1, 2, 3]) == {1, 2, 3}
    outputs = iter(["0, GPU-a, 1, 1, Disabled\n", ""])
    assert executor.busy_gpus([0]) == {0}
    outputs = iter(["0, GPU-a, [N/A], 0, Disabled\n", ""])
    with pytest.raises(InfrastructureError, match="invalid"):
        executor.busy_gpus([0])
    outputs = iter(["0, GPU-a, 1, 0, [N/A]\n", ""])
    with pytest.raises(InfrastructureError, match="unsupported"):
        executor.busy_gpus([0])
    outputs = iter(["0, GPU-a, 1, 0, Disabled\n", ""])
    with pytest.raises(InfrastructureError, match="omitted"):
        executor.busy_gpus([0, 1])


def test_idle_one_mib_gpus_dispatch_but_tiny_compute_process_keeps_card_busy(service, environment):
    _, _, docker = environment
    executor = DockerExecutor()
    idle = "".join(f"{index}, GPU-{index}, 1, 0, Disabled\n" for index in range(4))
    outputs = iter([idle, "", idle, "GPU-0\n"])
    calls = []

    def command(argv):
        calls.append(argv)
        return next(outputs)

    executor.command = command
    docker.busy_gpus = executor.busy_gpus
    job = submit(service, gpus=4)
    service.tick()
    assert service.get("trial-01", job["id"])["status"] == "running"
    assert '"device=0,1,2,3"' in docker.launched[0]
    assert "--query-gpu=index,uuid,memory.used,utilization.gpu,display_active" in calls[0]
    docker.finish(job["id"])
    waiting = submit(service, "trial-02", gpus=4)
    service.tick()
    assert service.get("trial-02", waiting["id"])["status"] == "queued"
    assert service.get("trial-02", waiting["id"])["gpu_seconds_reserved"] == 4 * 3600
    assert len(docker.launched) == 1 and not docker.killed


def test_cli_submit_wait_logs_and_errors(monkeypatch, capsys):
    calls = []
    responses = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return responses.pop(0)

    monkeypatch.setattr(compute_client, "request", request)
    responses.append({"job": {"id": 7, "status": "queued"}})
    assert compute_client.main(["submit", "--cpus", "120", "--memory-gib", "900", "--gpus", "4",
                                "--timeout", "30", "--workdir", "project", "--name", "training",
                                "--", "python", "train.py"]) == 0
    assert calls[0][2]["command"] == ["python", "train.py"]
    assert calls[0][2]["timeout_seconds"] == 30
    responses.extend([{"job": {"status": "running"}}, {"job": {"status": "failed", "id": 7}}])
    monkeypatch.setattr(compute_client.time, "sleep", lambda seconds: None)
    assert compute_client.main(["wait", "7"]) == 1
    responses.append({"text": "hello\n", "truncated": True})
    assert compute_client.main(["logs", "7"]) == 0
    assert "truncated" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        compute_client.main(["submit", "--", "x" * 32769])
    monkeypatch.setattr(compute_client, "request", lambda *args: (_ for _ in ()).throw(ValueError("HTTP 503")))
    assert compute_client.main(["status"]) == 1
    assert "HTTP 503" in capsys.readouterr().err


def test_cli_profile_transport_does_not_use_proxy(monkeypatch, tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"api_key": "private-test-key", "base_url": "https://unused.invalid/v1"}))
    monkeypatch.setattr(compute_client, "profile_path", lambda: profile)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"jobs":[]}'

    class Opener:
        def open(self, req, timeout):
            assert req.full_url == "http://127.0.0.1:18766/compute/jobs"
            assert req.get_header("Authorization") == "Bearer private-test-key"
            return Response()

    def opener(handler):
        assert handler.proxies == {}
        return Opener()

    monkeypatch.setattr(compute_client.urllib.request, "build_opener", opener)
    assert compute_client.request("GET", "/jobs") == {"jobs": []}
