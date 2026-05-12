"""Subprocess lifecycle coverage for the ``argus-skill`` daemon CLI."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from argus_skill.core import project


def _run_cli(
    *args: str,
    env: dict[str, str],
    cwd: Path,
    stdin: int | None = subprocess.DEVNULL,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus_skill", *args],
        cwd=cwd,
        env=env,
        stdin=stdin,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for daemon lifecycle condition")


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def test_daemon_lifecycle_via_subprocess(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    skills_dir = tmp_path / "skills"
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
        }
    )

    pid: int | None = None
    try:
        start = _run_cli("--daemon", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert start.returncode == 0, start

        _wait_until(lambda: pid_path.exists() and status_path.exists())
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        assert _pid_is_alive(pid)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == pid
        assert status["backend"] == "memory"
        assert Path(status["life_dir"]) == project_root

        live_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert live_status.returncode == 0, live_status
        assert "daemon   : alive" in live_status.stdout
        assert f"pid {pid}" in live_status.stdout

        second = _run_cli("--daemon", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert second.returncode == 2, second
        assert "already running" in second.stderr
        assert _read_pid(pid_path) == pid

        stop = _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists() and not _pid_is_alive(pid))

        final_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
    finally:
        if pid is not None:
            _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
            try:
                _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_repl_autospawn_pid_matches_status_sidecar(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SAFE_MODE": "1",
        }
    )

    pid: int | None = None
    try:
        run = _run_cli("--life-dir", str(global_root), env=env, cwd=repo_dir)
        output = run.stdout + run.stderr
        assert run.returncode == 0, run
        assert "daemon auto-spawned (pid 0)" not in output
        assert "daemon auto-spawned (pid " in output

        status = json.loads(
            (global_root / "projects" / project.project_fingerprint(repo_dir).fingerprint / "daemon.status.json").read_text(encoding="utf-8")
        )
        pid = int(status["pid"])
        assert pid > 0
        assert f"daemon auto-spawned (pid {pid})" in output
        assert f"pid {pid}" in output
    finally:
        if pid is not None:
            _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
            try:
                _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_follow_waits_for_fresh_events_file(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    events_path = global_root / "projects" / fingerprint / "events.jsonl"
    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "PYTHONUNBUFFERED": "1",
        }
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "argus_skill", "--follow", "--life-dir", str(global_root)],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None

        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.touch()
        time.sleep(0.4)
        assert proc.poll() is None

        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "life.mission.started", "item_id": "mission-1"}) + "\n")
        time.sleep(0.6)
        assert proc.poll() is None

        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert proc.returncode == 0, proc
        assert "not found" not in output
        assert "following" in output
        assert "waiting for" in output
        assert "started · item_id=mission-1" in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)
