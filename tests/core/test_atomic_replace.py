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


def test_posix_hosts_replace_once_without_retrying(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "new.json"
    source.write_text("new", encoding="utf-8")
    calls = []

    def refuse(src, dst):
        calls.append(src)
        raise PermissionError(13, "denied")

    monkeypatch.setattr(atomic_replace.sys, "platform", "linux")
    monkeypatch.setattr(atomic_replace.os, "replace", refuse)

    with pytest.raises(PermissionError):
        atomic_replace.replace(source, tmp_path / "manifest.json")
    assert len(calls) == 1
