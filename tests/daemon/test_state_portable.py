from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from argus.daemon import state as daemon_state
from argus.daemon.state import (
    read_continuous_state,
    write_continuous_config,
)


def test_failed_replace_preserves_existing_continuous_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    original = (tmp_path / "continuous.json").read_text(encoding="utf-8")
    real_replace = daemon_state.os.replace

    def _boom(src: str, dst: str) -> None:
        if str(dst).endswith("continuous.json"):
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(daemon_state.os, "replace", _boom)

    daemon_state.write_continuous_config(tmp_path, enabled=False, objective="new")

    assert (tmp_path / "continuous.json").read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_windows_continuous_lock_retries_until_acquired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    class _FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, _size: int) -> None:
            if mode == _FakeMsvcrt.LK_UNLCK:
                return
            assert os.fstat(fd).st_size >= 1
            attempts.append(mode)
            if len(attempts) < 3:
                raise OSError("busy")

    monkeypatch.setattr(daemon_state.os, "name", "nt")
    monkeypatch.setattr(daemon_state, "msvcrt", _FakeMsvcrt)
    monkeypatch.setattr(daemon_state.time, "sleep", sleeps.append)

    with daemon_state._continuous_config_lock(tmp_path):
        pass

    assert len(attempts) == 3
    assert sleeps == [
        daemon_state._WINDOWS_LOCK_POLL_SECONDS,
        daemon_state._WINDOWS_LOCK_POLL_SECONDS,
    ]


def test_replace_failure_after_callback_surfaces_instead_of_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    expected = read_continuous_state(tmp_path)
    real_replace = daemon_state.os.replace
    committed = tmp_path / "precommit.txt"

    def _fail_continuous_replace(src: str, dst: str) -> None:
        if str(dst).endswith("continuous.json"):
            raise OSError(errno.EIO, "replace failed")
        real_replace(src, dst)

    monkeypatch.setattr(daemon_state.os, "replace", _fail_continuous_replace)

    with pytest.raises(daemon_state.ContinuousConfigCommitError):
        daemon_state.compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="must not land",
            before_write=lambda: committed.write_text(
                "committed",
                encoding="utf-8",
            ),
        )

    assert committed.read_text(encoding="utf-8") == "committed"
    assert read_continuous_state(tmp_path) == expected
