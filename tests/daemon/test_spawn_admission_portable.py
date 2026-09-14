from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.daemon._life_worker_admission import (
    _acquire_daemon_spawn_lock,
    _release_daemon_spawn_lock,
)


def test_spawn_admission_lock_serializes_processes(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = (
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "import time\n"
        "from argus.daemon._life_worker_admission import "
        "_acquire_daemon_spawn_lock, _release_daemon_spawn_lock\n"
        f"config = SimpleNamespace(global_root={str(tmp_path)!r})\n"
        "lock = _acquire_daemon_spawn_lock(config)\n"
        f"Path({str(ready)!r}).write_text('ready', encoding='utf-8')\n"
        "time.sleep(1.0)\n"
        "_release_daemon_spawn_lock(lock)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.exists():
            stdout, stderr = child.communicate(timeout=2)
            pytest.fail(f"spawn-lock holder failed to start: {stdout}\n{stderr}")

        started = time.monotonic()
        lock = _acquire_daemon_spawn_lock(SimpleNamespace(global_root=tmp_path))
        elapsed = time.monotonic() - started
        _release_daemon_spawn_lock(lock)
        assert elapsed >= 0.5
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)
