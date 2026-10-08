from __future__ import annotations

import os

import pytest

from argus.core.scoped_file import open_regular_file


def test_pinned_parent_prevents_cross_project_link_swap_during_open(tmp_path, monkeypatch):
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("POSIX parent descriptor support required")
    parent = tmp_path / "allowed"
    foreign = tmp_path / "foreign"
    parent.mkdir()
    foreign.mkdir()
    (parent / "evidence.txt").write_text("allowed evidence")
    (foreign / "evidence.txt").write_text("foreign private data")
    original_open = os.open

    def swapped_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "evidence.txt" and dir_fd is not None:
            parent.rename(tmp_path / "pinned-original")
            parent.symlink_to(foreign, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapped_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, swapped_open})
    with open_regular_file(parent / "evidence.txt") as handle:
        assert handle.read() == b"allowed evidence"


def test_nonregular_named_pipe_is_rejected_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    path = tmp_path / "pipe"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        open_regular_file(path)


def _without_dir_fd(monkeypatch):
    """Exercise the path used where ``dir_fd`` is unavailable (Windows)."""
    monkeypatch.setattr(os, "supports_dir_fd", set(os.supports_dir_fd) - {os.open})


def test_platform_without_dir_fd_opens_a_canonical_regular_file(tmp_path, monkeypatch):
    _without_dir_fd(monkeypatch)
    path = tmp_path / "evidence.txt"
    path.write_bytes(b"line\r\nkept exactly\n")
    with open_regular_file(path) as handle:
        assert handle.read() == b"line\r\nkept exactly\n"
    with open_regular_file(tmp_path / "created.lock", os.O_RDWR | os.O_CREAT) as handle:
        handle.write(b"x")
    assert (tmp_path / "created.lock").read_bytes() == b"x"


def test_platform_without_dir_fd_rejects_a_parent_swapped_during_open(tmp_path, monkeypatch, require_symlink_support):
    _without_dir_fd(monkeypatch)
    parent = tmp_path / "allowed"
    foreign = tmp_path / "foreign"
    parent.mkdir()
    foreign.mkdir()
    (parent / "evidence.txt").write_text("allowed evidence")
    (foreign / "evidence.txt").write_text("foreign private data")
    original_open = os.open

    def swapped_open(path, flags, mode=0o777, **kwargs):
        if str(path).endswith("evidence.txt"):
            parent.rename(tmp_path / "pinned-original")
            parent.symlink_to(foreign, target_is_directory=True)
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    with pytest.raises(ValueError, match="symlink or reparse point"):
        open_regular_file(parent / "evidence.txt")


def test_platform_without_dir_fd_rejects_a_junction_but_not_a_cloud_placeholder(tmp_path, monkeypatch):
    from argus.core import scoped_file

    _without_dir_fd(monkeypatch)
    directory = tmp_path / "junction"
    directory.mkdir()
    (directory / "evidence.txt").write_text("data")
    real_lstat = os.lstat

    class _Reparse:
        def __init__(self, result, tag):
            self._result = result
            self.st_file_attributes = scoped_file._REPARSE_POINT
            self.st_reparse_tag = tag

        def __getattr__(self, name):
            return getattr(self._result, name)

    tag = {"value": 0xA0000003}  # junction (mount point)

    def lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        return _Reparse(result, tag["value"]) if os.fspath(path) == os.fspath(directory) else result

    monkeypatch.setattr(scoped_file.os, "lstat", lstat)
    with pytest.raises(ValueError, match="symlink or reparse point"):
        open_regular_file(directory / "evidence.txt")
    # A cloud-sync placeholder is a reparse point too, but not a redirection.
    tag["value"] = 0x9000001A
    with open_regular_file(directory / "evidence.txt") as handle:
        assert handle.read() == b"data"
