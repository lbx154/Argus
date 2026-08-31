import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.daemon import process


def _config(tmp_path: Path):
    root = tmp_path / "runtime"
    return SimpleNamespace(
        global_root=root,
        life_dir=root / "projects" / "session-1",
        project_workdir=tmp_path / "project",
        backend="codex",
        continuous=False,
        continuous_objective="",
        resume_continuous=False,
        continuous_open_ended=True,
    )


def test_frozen_windows_worker_reenters_same_binary(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(process.sys, "executable", r"C:\Argus\argus-core.exe")
    monkeypatch.setattr(process.sys, "frozen", True, raising=False)

    assert process._windows_daemon_command(config) == [
        r"C:\Argus\argus-core.exe",
        "--daemon-fg",
        "--life-dir",
        str(config.global_root),
        "--resume",
        "session-1",
        "--backend",
        "codex",
        "--mission-width",
        "2",
    ]


def test_source_windows_worker_uses_python_module(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(process.sys, "executable", "python.exe")
    monkeypatch.delattr(process.sys, "frozen", raising=False)

    command = process._windows_daemon_command(config)
    assert command[:3] == ["python.exe", "-m", "argus_skill"]
    assert command[-6:] == [
        "--resume",
        "session-1",
        "--backend",
        "codex",
        "--mission-width",
        "2",
    ]


def test_windows_background_worker_forces_utf8_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)
    pid_path = config.life_dir / "daemon.pid"
    status_path = config.life_dir / "daemon.status.json"
    pid_path.write_text("42", encoding="utf-8")
    status_path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    released: list[int | None] = []

    class FakeProcess:
        pid = 42
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(process.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        process,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=42,
            status_read_error="",
        ),
    )

    rc = process._spawn_windows_background_process(
        config,
        pid_path=pid_path,
        status_path=status_path,
        log_path=config.life_dir / "daemons" / "boot-test.log",
        spawn_lock_fd=7,
        release_spawn_lock=lambda fd: released.append(fd),
        quiet=True,
    )

    assert rc == 0
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    if os.name == "nt":
        assert captured["creationflags"] & process.subprocess.CREATE_NO_WINDOW
        startup = captured["startupinfo"]
        assert startup.dwFlags & process.subprocess.STARTF_USESHOWWINDOW
        assert startup.wShowWindow == process.subprocess.SW_HIDE
    assert released == [7]


def test_windows_background_worker_accepts_venv_launcher_descendant(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A Windows venv launcher PID differs from the Python runtime PID."""
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)
    pid_path = config.life_dir / "daemon.pid"
    status_path = config.life_dir / "daemon.status.json"
    pid_path.write_text("84", encoding="utf-8")
    status_path.write_text("{}", encoding="utf-8")
    clock = [0.0]
    reaped: list[int] = []

    class FakeProcess:
        pid = 42
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        process,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=84,
            status_read_error="",
        ),
    )
    monkeypatch.setattr(
        process,
        "_descendant_pids",
        lambda launcher_pid: (84,) if launcher_pid == 42 else (),
        raising=False,
    )
    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        process.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(process, "_WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(
        process,
        "_reap_failed_windows_spawn",
        lambda child: reaped.append(child.pid),
    )

    rc = process._spawn_windows_background_process(
        config,
        pid_path=pid_path,
        status_path=status_path,
        log_path=config.life_dir / "daemons" / "boot-test.log",
        spawn_lock_fd=7,
        release_spawn_lock=lambda _fd: None,
        quiet=True,
    )

    assert rc == 0
    assert reaped == []


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="native Windows process tree")
def test_native_windows_worker_accepts_descendant_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)
    pid_path = config.life_dir / "daemon.pid"
    status_path = config.life_dir / "daemon.status.json"
    worker_script = (
        "import json, os, time\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from argus_skill.core.daemon_lock import acquire_global_daemon_lock\n"
        f"life = Path({str(config.life_dir)!r})\n"
        "lock = acquire_global_daemon_lock(pid_path=life / 'daemon.pid')\n"
        "started = datetime.now(timezone.utc).isoformat()\n"
        "(life / 'daemon.status.json').write_text(json.dumps({"
        "'pid': os.getpid(), 'started_at_iso': started}), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    launcher_script = (
        "import subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', {worker_script!r}])\n"
        "raise SystemExit(child.wait())\n"
    )
    real_popen = process.subprocess.Popen
    spawned = []

    def capture_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(process.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(
        process,
        "_windows_daemon_command",
        lambda _config: [process.sys.executable, "-c", launcher_script],
    )

    try:
        rc = process._spawn_windows_background_process(
            config,
            pid_path=pid_path,
            status_path=status_path,
            log_path=config.life_dir / "daemons" / "boot-native.log",
            spawn_lock_fd=7,
            release_spawn_lock=lambda _fd: None,
            quiet=True,
        )

        status = process.read_daemon_status(config.life_dir)
        assert rc == 0
        assert status.alive and status.pid is not None
        assert status.pid != spawned[0].pid
        assert status.pid in process._descendant_pids(spawned[0].pid)
    finally:
        if spawned:
            process._reap_failed_windows_spawn(spawned[0])


def test_windows_background_worker_preserves_early_exit_code(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)
    released: list[int | None] = []

    class FakeProcess:
        pid = 42
        returncode = 3

        def poll(self):
            return self.returncode

    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    rc = process._spawn_windows_background_process(
        config,
        pid_path=config.life_dir / "daemon.pid",
        status_path=config.life_dir / "daemon.status.json",
        log_path=config.life_dir / "daemons" / "boot-test.log",
        spawn_lock_fd=7,
        release_spawn_lock=lambda fd: released.append(fd),
        quiet=True,
    )

    assert rc == 3
    assert released == [7]


def test_windows_background_worker_rejects_clean_exit_without_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)

    class FakeProcess:
        pid = 42
        returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    rc = process._spawn_windows_background_process(
        config,
        pid_path=config.life_dir / "daemon.pid",
        status_path=config.life_dir / "daemon.status.json",
        log_path=config.life_dir / "daemons" / "boot-test.log",
        spawn_lock_fd=7,
        release_spawn_lock=lambda _fd: None,
        quiet=True,
    )

    assert rc == 2


def test_windows_background_worker_rejects_foreign_status_and_reaps_spawn(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.project_workdir.mkdir()
    config.life_dir.mkdir(parents=True)
    pid_path = config.life_dir / "daemon.pid"
    status_path = config.life_dir / "daemon.status.json"
    pid_path.write_text("99", encoding="utf-8")
    status_path.write_text("{}", encoding="utf-8")
    clock = [0.0]
    reaped: list[int] = []

    class FakeProcess:
        pid = 42
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(
        process,
        "_windows_runtime_belongs_to_launcher",
        lambda _launcher_pid, _runtime_pid: False,
    )
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        process,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=99,
            status_read_error="",
        ),
    )
    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        process.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(process, "_WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(
        process,
        "_reap_failed_windows_spawn",
        lambda child: reaped.append(child.pid),
    )

    rc = process._spawn_windows_background_process(
        config,
        pid_path=pid_path,
        status_path=status_path,
        log_path=config.life_dir / "daemons" / "boot-test.log",
        spawn_lock_fd=7,
        release_spawn_lock=lambda _fd: None,
        quiet=True,
    )

    assert rc == 2
    assert reaped == [42]


def test_failed_windows_spawn_reaps_exact_popen_tree(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeProcess:
        pid = 42
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            calls.append(("wait", int(timeout)))
            return self.returncode

        def terminate(self):
            raise AssertionError("tree termination succeeded; root fallback is unnecessary")

    child = FakeProcess()

    def terminate_tree(pid: int, *, identity_check) -> bool:
        assert identity_check() is True
        calls.append(("tree", pid))
        child.returncode = 1
        return True

    monkeypatch.setattr(process, "_terminate_windows_process_tree", terminate_tree)

    process._reap_failed_windows_spawn(child)

    assert calls == [("tree", 42), ("wait", 5)]
