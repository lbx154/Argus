from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.core import atomic_replace


def test_a_transient_windows_sharing_violation_is_waited_out(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "new.json"
    target = tmp_path / "manifest.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace
    attempts = []

    def held_twice(src, dst):
        attempts.append(src)
        if len(attempts) < 3:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_replace.sys, "platform", "win32")
    monkeypatch.setattr(atomic_replace.os, "replace", held_twice)

    atomic_replace.replace(source, target)

    assert len(attempts) == 3
    assert target.read_text(encoding="utf-8") == "new"


def test_a_persistent_refusal_still_surfaces(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "new.json"
    source.write_text("new", encoding="utf-8")

    def refuse(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(atomic_replace.sys, "platform", "win32")
    monkeypatch.setattr(atomic_replace.os, "replace", refuse)
    monkeypatch.setattr(atomic_replace, "_RETRY_SECONDS", 0.05)

    with pytest.raises(PermissionError):
        atomic_replace.replace(source, tmp_path / "manifest.json")


def test_write_atomic_replaces_the_file_and_leaves_no_temporary(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    atomic_replace.write_atomic(target, "new\n")
    atomic_replace.write_atomic(tmp_path / "raw.bin", b"a\r\nb")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert (tmp_path / "raw.bin").read_bytes() == b"a\r\nb"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["raw.bin", "state.json"]


def test_write_atomic_failure_keeps_the_old_file_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_replace.os, "replace", refuse)
    with pytest.raises(OSError):
        atomic_replace.write_atomic(target, "new", fsync=True)

    assert target.read_text(encoding="utf-8") == "old"
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_write_atomic_mode_is_exact_and_default_follows_umask(tmp_path: Path) -> None:
    previous = os.umask(0o022)
    try:
        atomic_replace.write_atomic(tmp_path / "default", "x")
        atomic_replace.write_atomic(tmp_path / "private", "x", mode=0o600)
        atomic_replace.write_atomic(tmp_path / "shared", "x", mode=0o644)
    finally:
        os.umask(previous)

    assert (tmp_path / "default").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "shared").stat().st_mode & 0o777 == 0o644
