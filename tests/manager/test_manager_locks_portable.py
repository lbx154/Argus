from __future__ import annotations

import errno
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import portalocker
import pytest

from argus.manager._session_ops import (
    _SESSION_FILE,
    _acquire_session_lock,
    _ManagerSession,
)

_CHILD_LOCK_HOLDER = r"""
import sys
import time
from pathlib import Path

kind, root_raw, marker_raw, release_raw = sys.argv[1:]
root = Path(root_raw)
marker = Path(marker_raw)
release = Path(release_raw)
if kind == "session":
    from contextlib import contextmanager

    import portalocker

    @contextmanager
    def turn_on_the_shared_thread():
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".manager_session.lock").open("a+b") as handle:
            portalocker.lock(handle, portalocker.LOCK_EX)
            yield

    lock = turn_on_the_shared_thread()
elif kind == "pipeline":
    from argus.manager._session_ops import manager_pipeline_lock
    lock = manager_pipeline_lock(root)
elif kind == "control":
    from argus.manager.control_state import CampaignControlStore
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


def test_a_held_manager_session_never_blocks_another_turn(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session-busy"
    root.mkdir()
    shared = {"thread_id": "shared-thread", "recent_turns": [
        {"kind": "decision", "request_excerpt": "Pause training?", "answer_excerpt": "Keep training."},
    ]}
    (root / _SESSION_FILE).write_text(json.dumps(shared), encoding="utf-8")

    class _Runner:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.prompts.append(prompt)
            assert resume_thread_id is None
            return SimpleNamespace(thread_id="fresh-thread")

    runner = _Runner()
    with _held_by_child(root, "session"):
        started = time.monotonic()
        result = _ManagerSession(runner, root).run_exec(
            prompt="route this request",
            options=None,
            run_label="manager-route",
        )
        # The holder never releases while this turn runs: it answered at once.
        assert time.monotonic() - started < 10

    # A fresh thread that still carries the saved turns; the shared thread
    # stays with its holder.
    assert result.thread_id == "fresh-thread"
    assert "Keep training." in runner.prompts[0] and "route this request" in runner.prompts[0]
    assert json.loads((root / _SESSION_FILE).read_text(encoding="utf-8")) == shared


@pytest.mark.parametrize("error_code", [errno.EBADF, errno.ENOLCK, errno.EIO])
@pytest.mark.parametrize("wrapped", [False, True])
def test_lock_failures_are_reported_without_contention_retry(
    tmp_path, monkeypatch, error_code, wrapped,
) -> None:
    cause = OSError(error_code, "locking unavailable")
    error = portalocker.exceptions.LockException(cause) if wrapped else cause
    calls = []

    def fail_lock(*_args):
        calls.append(True)
        raise error

    monkeypatch.setattr("argus.manager._session_ops.portalocker.lock", fail_lock)
    monkeypatch.setattr(
        "argus.manager._session_ops.time.sleep",
        lambda _seconds: pytest.fail("a lock failure must not be retried as contention"),
    )
    with (tmp_path / "failed.lock").open("a+b") as handle:
        with pytest.raises(type(error)) as caught:
            _acquire_session_lock(handle)
    assert caught.value is error
    assert len(calls) == 1
