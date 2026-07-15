from __future__ import annotations

import errno
import importlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _module():
    return importlib.import_module("argus_skill.tools.atomic_artifact")


def test_atomic_write_fsyncs_file_then_replaced_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    target = tmp_path / "research" / "SCOPE.md"
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracked_fsync(fd: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        events.append(f"fsync:{kind}")
        real_fsync(fd)

    def tracked_replace(source, destination) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "fsync", tracked_fsync)
    monkeypatch.setattr(module.os, "replace", tracked_replace)

    module.atomic_write_text(target, "first checkpoint\n")

    assert target.read_text(encoding="utf-8") == "first checkpoint\n"
    assert events == ["fsync:file", "replace", "fsync:directory"]
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_failed_replace_preserves_previous_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    target = tmp_path / "research" / "SCOPE.md"
    module.atomic_write_text(target, "<!-- status: incomplete -->\nold\n")

    def fail_replace(*_args) -> None:
        raise OSError("simulated interruption before rename")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated interruption"):
        module.atomic_append_text(target, "new section\n")

    assert target.read_text(encoding="utf-8") == (
        "<!-- status: incomplete -->\nold\n"
    )
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_atomic_append_builds_on_existing_artifact(tmp_path: Path) -> None:
    module = _module()
    target = tmp_path / "research" / "SCOPE.md"

    module.atomic_write_text(target, "<!-- status: incomplete -->\n")
    module.atomic_append_text(target, "## Precise statement\nfixed\n")
    module.atomic_append_text(target, "## Literature status\nverified\n")

    assert target.read_text(encoding="utf-8") == (
        "<!-- status: incomplete -->\n"
        "## Precise statement\nfixed\n"
        "## Literature status\nverified\n"
    )


def test_unsupported_directory_fsync_does_not_report_committed_append_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    target = tmp_path / "research" / "SCOPE.md"
    module.atomic_write_text(target, "first\n")
    real_fsync = os.fsync

    def unsupported_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", unsupported_directory_fsync)

    module.atomic_append_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


def test_cli_reads_small_write_and_append_chunks_from_stdin(tmp_path: Path) -> None:
    target = tmp_path / "research" / "SCOPE.md"
    command = [
        sys.executable,
        "-m",
        "argus_skill.tools.atomic_artifact",
    ]

    first = subprocess.run(
        [*command, "write", str(target)],
        input="<!-- status: incomplete -->\n",
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [*command, "append", str(target)],
        input="## Precise statement\nfixed\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert target.read_text(encoding="utf-8") == (
        "<!-- status: incomplete -->\n"
        "## Precise statement\nfixed\n"
    )
