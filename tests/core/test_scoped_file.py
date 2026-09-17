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
