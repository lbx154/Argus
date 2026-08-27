from __future__ import annotations

import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from argus_skill.manager._session_ops import (
    _ManagerSession,
    manager_pipeline_lock,
    manager_session_lock,
)
from argus_skill.manager.control_state import CampaignControlStore

_CHILD_LOCK_HOLDER = r"""
import sys
import time
from pathlib import Path

kind, root_raw, marker_raw, release_raw = sys.argv[1:]
root = Path(root_raw)
marker = Path(marker_raw)
release = Path(release_raw)
if kind == "session":
    from argus_skill.manager._session_ops import manager_session_lock
    lock = manager_session_lock(root)
elif kind == "pipeline":
    from argus_skill.manager._session_ops import manager_pipeline_lock
    lock = manager_pipeline_lock(root)
elif kind == "control":
    from argus_skill.manager.control_state import CampaignControlStore
    lock = CampaignControlStore(root).locked()
else:
    raise ValueError(kind)

with lock:
    marker.write_text(str(__import__("os").getpid()), encoding="utf-8")
    deadline = time.monotonic() + 15.0
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("parent did not release child lock holder")
        time.sleep(0.01)
"""


@contextmanager
def _held_by_child(root: Path, kind: str) -> Iterator[Path]:
    marker = root.parent / f"{kind}.locked"
    release = root.parent / f"{kind}.release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CHILD_LOCK_HOLDER,
            kind,
            str(root),
            str(marker),
            str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 10.0
        while not marker.is_file():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(
                    f"{kind} lock holder exited before acquiring the lock: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(f"{kind} lock holder did not acquire within 10s")
            time.sleep(0.02)
        yield release
    finally:
        release.write_text("release", encoding="utf-8")
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        if process.returncode != 0:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"{kind} lock holder failed: stdout={stdout!r} stderr={stderr!r}"
            )


@pytest.mark.parametrize(
    ("kind", "lock_factory"),
    [
        ("session", manager_session_lock),
        ("pipeline", manager_pipeline_lock),
    ],
)
def test_manager_locks_wait_for_a_real_peer_process(
    tmp_path: Path,
    kind: str,
    lock_factory,
) -> None:
    root = tmp_path / kind
    with _held_by_child(root, kind) as release:
        releaser = threading.Thread(
            target=lambda: (time.sleep(0.35), release.write_text("release", encoding="utf-8"))
        )
        releaser.start()
        started = time.monotonic()
        with lock_factory(root):
            pass
        elapsed = time.monotonic() - started
        releaser.join(timeout=2)
        assert elapsed >= 0.25

    # Releasing the peer makes the exact same lock immediately usable.
    with lock_factory(root):
        pass


def test_manager_session_waits_then_uses_the_shared_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session-wait"
    omitted = object()

    class _Runner:
        def __init__(self) -> None:
            self.resume_values: list[object] = []

        def run_exec(
            self,
            *,
            prompt,
            options,
            run_label,
            resume_thread_id=omitted,
        ):
            self.resume_values.append(resume_thread_id)
            return SimpleNamespace(thread_id="fresh-thread")

    runner = _Runner()
    with _held_by_child(root, "session") as release:
        releaser = threading.Thread(
            target=lambda: (time.sleep(0.35), release.write_text("release", encoding="utf-8"))
        )
        releaser.start()
        result = _ManagerSession(runner, root).run_exec(
            prompt="route this request",
            options=None,
            run_label="manager-route",
        )
        releaser.join(timeout=2)

    assert result.thread_id == "fresh-thread"
    assert runner.resume_values == [None]


def test_campaign_control_lock_blocks_until_real_peer_process_releases(
    tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    with _held_by_child(root, "control") as release:
        def release_later() -> None:
            time.sleep(0.35)
            release.write_text("release", encoding="utf-8")

        releaser = threading.Thread(target=release_later)
        releaser.start()
        started = time.monotonic()
        with CampaignControlStore(root).locked():
            elapsed = time.monotonic() - started
        releaser.join(timeout=2)

    assert elapsed >= 0.25
