"""Built-in refresh survives Windows share locks without losing the old file."""

import os
from pathlib import Path

import pytest

from argus_skill.skills import builtins


@pytest.mark.parametrize("old_bytes", [b"old factory text\n", b"\xff old non-UTF8 file\n"])
def test_transient_share_lock_allows_replacement_of_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_bytes: bytes,
) -> None:
    destination = tmp_path / "seed.md"
    destination.write_bytes(old_bytes)
    real_replace = os.replace
    attempted = []

    def replace(source, target):
        attempted.append(source)
        if len(attempted) == 1:
            raise PermissionError("temporary Windows share lock")
        return real_replace(source, target)

    monkeypatch.setattr(builtins.os, "replace", replace)
    monkeypatch.setattr(builtins.time, "sleep", lambda _seconds: None)

    builtins._atomic_write_text(destination, "new factory text\n")

    assert destination.read_bytes() == b"new factory text\n"
    assert len(attempted) > 1
    assert list(tmp_path.glob("seed.md.tmp.*")) == []


def test_persistent_share_lock_preserves_original_and_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "seed.md"
    original = b"\xff preserve these exact bytes\r\n"
    destination.write_bytes(original)
    attempts = []

    def replace(_source, _target):
        attempts.append(None)
        assert len(attempts) <= 16, "a persistent lock must not retry indefinitely"
        raise PermissionError("persistent Windows share lock")

    monkeypatch.setattr(builtins.os, "replace", replace)
    monkeypatch.setattr(builtins.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError, match="persistent Windows share lock"):
        builtins._atomic_write_text(destination, "new factory text\n")

    assert len(attempts) > 1
    assert destination.read_bytes() == original
    assert list(tmp_path.glob("seed.md.tmp.*")) == []


def test_identical_crlf_winner_finishes_without_repeated_replace_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "seed.md"
    destination.write_bytes(b"same factory text\r\n")
    attempts = []

    def replace(_source, _target):
        attempts.append(None)
        raise PermissionError("another process already installed this seed")

    def unexpected_sleep(_seconds):
        pytest.fail("an identical concurrent winner needs no retry")

    monkeypatch.setattr(builtins.os, "replace", replace)
    monkeypatch.setattr(builtins.time, "sleep", unexpected_sleep)

    builtins._atomic_write_text(destination, "same factory text\n")

    assert len(attempts) == 1
    assert destination.read_bytes() == b"same factory text\r\n"
    assert list(tmp_path.glob("seed.md.tmp.*")) == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file-sharing semantics")
def test_real_windows_share_lock_is_retried_after_the_holder_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    destination = tmp_path / "seed.md"
    destination.write_bytes(b"old factory text\n")
    # GENERIC_READ + FILE_SHARE_READ deliberately deny FILE_SHARE_DELETE.
    handle = kernel32.CreateFileW(str(destination), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    real_replace = os.replace
    native_errors = []

    def replace(source, target):
        nonlocal handle
        try:
            return real_replace(source, target)
        except PermissionError as exc:
            native_errors.append(exc.winerror)
            # Release only after the OS has refused the real rename. This
            # avoids a timer race while exercising the native sharing error.
            if handle is not None:
                assert kernel32.CloseHandle(handle)
                handle = None
            raise

    monkeypatch.setattr(builtins.os, "replace", replace)
    try:
        builtins._atomic_write_text(destination, "new factory text\n")
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)

    assert native_errors and all(error in {5, 32, 33} for error in native_errors)
    assert destination.read_bytes() == b"new factory text\n"
    assert list(tmp_path.glob("seed.md.tmp.*")) == []
