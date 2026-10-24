from __future__ import annotations

import errno
import json
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
        if dst.endswith("continuous.json"):
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(daemon_state.os, "replace", _boom)

    daemon_state.write_continuous_config(tmp_path, enabled=False, objective="new")

    assert (tmp_path / "continuous.json").read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("continuous.json.*.tmp")) == []


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


def test_quota_exhausted_cas_uses_preallocated_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    expected = read_continuous_state(tmp_path)
    real_atomic_write = daemon_state._atomic_write_text
    failures = 0

    def _quota_once(path: Path, text: str) -> None:
        nonlocal failures
        if path.name == "continuous.json" and failures == 0:
            failures += 1
            raise OSError(errno.ENOSPC, "quota exhausted")
        real_atomic_write(path, text)

    monkeypatch.setattr(daemon_state, "_atomic_write_text", _quota_once)

    assert daemon_state.compare_and_swap_continuous_config(
        tmp_path,
        expected=expected,
        enabled=True,
        objective="after reserve",
    )

    assert failures == 1
    state = read_continuous_state(tmp_path)
    assert state.objective == "after reserve"
    json.loads((tmp_path / "continuous.json").read_text(encoding="utf-8"))
    assert all(path.exists() for path in daemon_state._continuous_reserve_paths(tmp_path))


def test_delayed_quota_failure_before_replace_leaves_current_json_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    expected = read_continuous_state(tmp_path)
    precommits: list[str] = []

    def _always_delayed_quota(
        path: Path,
        _text: str,
        *,
        before_replace=None,
    ) -> None:
        if path.name == "continuous.json":
            raise OSError(errno.EDQUOT, "delayed quota failure")
        raise AssertionError("unexpected non-continuous write")

    monkeypatch.setattr(daemon_state, "_atomic_write_text", _always_delayed_quota)

    assert (
        daemon_state.compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="must not land",
            before_write=lambda: precommits.append("committed"),
        )
        is False
    )

    assert precommits == []
    assert read_continuous_state(tmp_path) == expected
    assert json.loads((tmp_path / "continuous.json").read_text(encoding="utf-8"))[
        "objective"
    ] == "existing"
    assert any(path.exists() for path in daemon_state._continuous_reserve_paths(tmp_path))


def test_post_replace_failure_surfaces_instead_of_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    expected = read_continuous_state(tmp_path)
    real_replace = daemon_state.os.replace

    def _replace_then_fail(src: str, dst: str) -> None:
        real_replace(src, dst)
        if dst.endswith("continuous.json"):
            raise OSError(errno.EIO, "directory fsync failed after replace")

    monkeypatch.setattr(daemon_state.os, "replace", _replace_then_fail)

    with pytest.raises(daemon_state.ContinuousConfigWriteAfterReplaceError):
        daemon_state.compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="landed despite error",
        )

    state = read_continuous_state(tmp_path)
    assert state.objective == "landed despite error"
    assert state.generation == expected.generation + 1
    json.loads((tmp_path / "continuous.json").read_text(encoding="utf-8"))


def test_replace_failure_after_callback_surfaces_instead_of_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    expected = read_continuous_state(tmp_path)
    real_replace = daemon_state.os.replace
    committed = tmp_path / "precommit.txt"

    def _fail_continuous_replace(src: str, dst: str) -> None:
        if dst.endswith("continuous.json"):
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


def test_transient_reserve_refresh_failure_preserves_existing_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="existing")
    before = {
        path.name: path.stat().st_size
        for path in daemon_state._continuous_reserve_paths(tmp_path)
    }
    real_atomic_bytes = daemon_state._atomic_write_bytes

    def _fail_reserve_refresh(path: Path, data: bytes) -> None:
        if path.name in daemon_state._CONTINUOUS_RESERVE_NAMES:
            raise OSError(errno.EIO, "transient reserve failure")
        real_atomic_bytes(path, data)

    monkeypatch.setattr(daemon_state, "_atomic_write_bytes", _fail_reserve_refresh)

    with daemon_state._continuous_config_lock(tmp_path):
        daemon_state._ensure_continuous_reserve_unlocked(
            tmp_path,
            "x" * (daemon_state._CONTINUOUS_RESERVE_MIN_BYTES * 2),
        )

    after = {
        path.name: path.stat().st_size
        for path in daemon_state._continuous_reserve_paths(tmp_path)
    }
    assert after == before
