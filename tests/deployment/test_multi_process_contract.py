from __future__ import annotations

import json
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from argus_skill.release import release_manifest


def _reserve_worker(root: str, project: str, start, finish, queue, call_id: str) -> None:
    from argus_skill.core.cost_control import reserve_call_budget

    start.wait()
    reservation, reason = reserve_call_budget(
        call_id=call_id,
        project_root=Path(project),
        mission_id="mission-1",
        provider="codex",
        model="gpt-5.6-sol",
        run_label="engineer-r1",
        global_root=Path(root),
        per_mission_cap_usd=10,
        project_daily_cap_usd=10,
        global_daily_cap_usd=10,
    )
    queue.put((reservation is not None, reason))
    if reservation is not None:
        finish.wait(timeout=10)
        reservation.release(reason="deployment-test")


def _command_worker(root: str, start, queue, marker: str) -> None:
    from argus_skill.daemon.commands import execute_daemon_command

    start.wait()

    def handler():
        fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, b"executed\n")
        finally:
            os.close(fd)
        time.sleep(0.2)
        return {"rc": 0}

    receipt = execute_daemon_command(
        Path(root),
        operation="start",
        handler=handler,
        command_id="same-command",
    )
    queue.put(receipt.status)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str) -> tuple[dict, object]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read()), response.headers


def test_processes_cannot_reserve_the_same_budget(tmp_path: Path) -> None:
    context = mp.get_context("fork")
    project = tmp_path / "projects" / "p1"
    project.mkdir(parents=True)
    start = context.Event()
    finish = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_reserve_worker,
            args=(str(tmp_path), str(project), start, finish, queue, f"call-{index}"),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=10) for _ in processes]
    finish.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sum(allowed for allowed, _reason in results) == 1
    assert any("budget exhausted" in reason for allowed, reason in results if not allowed)


def test_processes_claim_duplicate_daemon_command_once(tmp_path: Path) -> None:
    context = mp.get_context("fork")
    start = context.Event()
    queue = context.Queue()
    marker = tmp_path / "handler.log"
    processes = [
        context.Process(
            target=_command_worker,
            args=(str(tmp_path), start, queue, str(marker)),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    statuses = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert marker.read_text().splitlines() == ["executed"]
    assert "applied" in statuses
    assert set(statuses) <= {"running", "applied"}


def test_real_webapi_process_exposes_release_protocol_metrics_and_projects(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "s-deploy"
    project.mkdir(parents=True)
    (project / "events.jsonl").touch()
    (project / "backlog.jsonl").touch()
    (project / "session.json").write_text(
        json.dumps({"id": "s-deploy", "last_active": time.time()}),
        encoding="utf-8",
    )
    port = _free_port()
    source_root = Path(__file__).parents[2]
    code = (
        "from pathlib import Path; import uvicorn; "
        "from argus_skill.webapi.server import create_app; "
        f"uvicorn.run(create_app(global_root=Path({str(tmp_path)!r})), "
        f"host='127.0.0.1', port={port}, log_level='error')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    env["ARGUS_SKILL_SOURCE_ROOT"] = str(source_root)
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=source_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                meta, headers = _get_json(base + "/api/meta")
                break
            except Exception:
                if process.poll() is not None or time.monotonic() >= deadline:
                    stderr = process.stderr.read() if process.stderr else ""
                    raise AssertionError(f"WebAPI failed to start: {stderr}")
                time.sleep(0.1)
        assert meta["runtime"]["release_id"] == release_manifest()["release_id"]
        assert meta["runtime"]["release_matches_source"] is True
        assert headers["X-Argus-Release"] == release_manifest()["release_id"]
        projects, _ = _get_json(base + "/api/projects?include_empty=true")
        assert [row["id"] for row in projects["projects"]] == ["s-deploy"]
        metrics, _ = _get_json(base + "/api/metrics")
        assert metrics["slo"]["status"] == "healthy"
        with urllib.request.urlopen(base + "/metrics", timeout=2) as response:
            assert "argus_slo_healthy" in response.read().decode()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
