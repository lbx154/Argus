from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tarfile
import time
import venv
import zipfile
from pathlib import Path

from argus_skill.core import project


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_cli(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "argus-skill.exe"
    return venv_dir / "bin" / "argus-skill"


def _artifact_contains(wheel: Path, member_suffix: str) -> bool:
    with zipfile.ZipFile(wheel) as zf:
        return any(name.endswith(member_suffix) for name in zf.namelist())


def _sdist_contains(sdist: Path, member_suffix: str) -> bool:
    with tarfile.open(sdist, "r:gz") as tf:
        return any(member.name.endswith(member_suffix) for member in tf.getmembers())


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


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


def test_built_artifacts_and_installed_cli_contract(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    sdist = next(outdir.glob("*.tar.gz"))
    assert _artifact_contains(wheel, "argus_skill/skills/__init__.py")
    assert _artifact_contains(wheel, "argus_skill/skills/store.py")
    assert _sdist_contains(sdist, "argus_skill/skills/__init__.py")
    assert _sdist_contains(sdist, "argus_skill/skills/store.py")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=repo)

    cli = _venv_cli(venv_dir)
    runtime_cwd = tmp_path / "runtime"
    runtime_cwd.mkdir()

    help_run = _run([str(cli), "--help"], cwd=runtime_cwd)
    assert help_run.returncode == 0

    import_run = _run(
        [str(venv_python), "-c", "import argus_skill.skills"],
        cwd=runtime_cwd,
    )
    assert import_run.returncode == 0

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    runtime_fp = project.project_fingerprint(runtime_cwd).fingerprint
    watch_project_root = life_dir / "projects" / runtime_fp
    watch_project_root.mkdir(parents=True)
    status_run = _run(
        [str(cli), "--status", "--life-dir", str(life_dir)],
        cwd=runtime_cwd,
    )
    assert status_run.returncode == 0

    watch_life_dir = tmp_path / "watch-life"
    watch_life_dir.mkdir()
    watch_project_root = watch_life_dir / "projects" / runtime_fp
    watch_project_root.mkdir(parents=True)
    watch_fallback = subprocess.run(
        [str(cli), "--watch", "--life-dir", str(watch_life_dir)],
        cwd=runtime_cwd,
        text=True,
        capture_output=True,
    )
    assert watch_fallback.returncode == 2
    assert "watch: rich is required for the live cockpit" in watch_fallback.stderr

    missing_dir = tmp_path / "missing-life"
    missing_project_root = missing_dir / "projects" / runtime_fp
    watch_run = subprocess.run(
        [str(cli), "--watch", "--life-dir", str(missing_dir)],
        cwd=runtime_cwd,
        text=True,
        capture_output=True,
    )
    assert watch_run.returncode == 2
    assert f"watch: life-dir not found: {missing_project_root}" in watch_run.stderr
    assert "Traceback" not in watch_run.stderr


def test_installed_cli_daemon_lifecycle(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"
    _run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=repo,
    )

    wheel = next(outdir.glob("*.whl"))
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_python(venv_dir)
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=tmp_path)

    cli = _venv_cli(venv_dir)
    runtime_cwd = tmp_path / "runtime"
    runtime_cwd.mkdir()
    home_dir = tmp_path / "home"
    skills_dir = tmp_path / "skills"
    home_dir.mkdir()
    skills_dir.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "ARGUS_SKILL_LIFE_BACKEND": "memory",
            "ARGUS_SKILL_SKILLS_DIR": str(skills_dir),
            "HOME": str(home_dir),
        }
    )

    life_dir = tmp_path / "life"
    runtime_fp = project.project_fingerprint(runtime_cwd).fingerprint
    project_root = life_dir / "projects" / runtime_fp
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    pid: int | None = None

    try:
        start = _run(
            [str(cli), "--daemon", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert start.returncode == 0, start

        _wait_until(lambda: pid_path.exists() and status_path.exists())
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        assert _pid_is_alive(pid)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == pid
        assert status["backend"] == "memory"
        assert status["life_dir"] == str(project_root)
        assert "started_at_iso" in status

        live_status = _run(
            [str(cli), "--status", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert live_status.returncode == 0, live_status
        assert f"pid {pid}" in live_status.stdout
        assert "backend memory" in live_status.stdout

        second = subprocess.run(
            [str(cli), "--daemon", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
        )
        assert second.returncode == 2, second
        assert "already running" in second.stderr
        assert _read_pid(pid_path) == pid

        stop = _run(
            [str(cli), "--daemon-stop", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists() and not _pid_is_alive(pid))

        final_status = _run(
            [str(cli), "--status", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
        )
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
    finally:
        cleanup_pid = pid or _read_pid(pid_path)
        subprocess.run(
            [str(cli), "--daemon-stop", "--life-dir", str(life_dir)],
            cwd=runtime_cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if cleanup_pid is not None and _pid_is_alive(cleanup_pid):
            try:
                _wait_until(lambda: not _pid_is_alive(cleanup_pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(cleanup_pid, signal.SIGKILL)
                except OSError:
                    pass
                _wait_until(lambda: not _pid_is_alive(cleanup_pid), timeout=10.0)
        if pid_path.exists() or status_path.exists():
            _wait_until(lambda: not pid_path.exists() and not status_path.exists(), timeout=10.0)
