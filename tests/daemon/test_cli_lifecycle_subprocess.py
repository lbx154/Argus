"""Subprocess lifecycle coverage for the ``argus-skill`` daemon CLI."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _run_cli(*args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus_skill", *args],
        cwd=cwd,
        env=env,
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
    repo = Path(__file__).resolve().parents[2]
    life_dir = tmp_path / "life"
    skills_dir = tmp_path / "skills"
    pid_path = life_dir / "daemon.pid"
    status_path = life_dir / "daemon.status.json"
    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
        }
    )

    pid: int | None = None
    try:
        start = _run_cli("--daemon", "--life-dir", str(life_dir), env=env, cwd=repo)
        assert start.returncode == 0, start

        _wait_until(lambda: pid_path.exists() and status_path.exists())
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        assert _pid_is_alive(pid)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == pid
        assert status["backend"] == "memory"
        assert Path(status["life_dir"]) == life_dir

        live_status = _run_cli("--status", "--life-dir", str(life_dir), env=env, cwd=repo)
        assert live_status.returncode == 0, live_status
        assert "daemon   : alive" in live_status.stdout
        assert f"pid {pid}" in live_status.stdout

        second = _run_cli("--daemon", "--life-dir", str(life_dir), env=env, cwd=repo)
        assert second.returncode == 2, second
        assert "already running" in second.stderr
        assert _read_pid(pid_path) == pid

        stop = _run_cli("--daemon-stop", "--life-dir", str(life_dir), env=env, cwd=repo)
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists() and not _pid_is_alive(pid))

        final_status = _run_cli("--status", "--life-dir", str(life_dir), env=env, cwd=repo)
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
    finally:
        if pid is not None:
            _run_cli("--daemon-stop", "--life-dir", str(life_dir), env=env, cwd=repo)
            try:
                _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
