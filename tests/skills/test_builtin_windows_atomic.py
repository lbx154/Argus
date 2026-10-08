"""Built-in refresh survives Windows share locks without losing the old file."""

import os
from pathlib import Path
from uuid import UUID

import pytest

from argus.skills import builtins


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
    assert list(tmp_path.iterdir()) == [destination]


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
    assert list(tmp_path.iterdir()) == [destination]


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
    assert list(tmp_path.iterdir()) == [destination]


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
    assert list(tmp_path.iterdir()) == [destination]


def test_long_reference_filename_does_not_expand_temporary_path(tmp_path, monkeypatch):
    parent = tmp_path / ("p" * (200 - len(str(tmp_path)) - 1))
    parent.mkdir()
    destination = parent / "architectural_operator_substitution.md"
    identifier = UUID(int=3)
    monkeypatch.setattr(builtins.uuid, "uuid4", lambda: identifier)
    old_temporary = destination.with_name(destination.name + ".tmp.59760.10110.bb4119ae")
    assert len(str(old_temporary)) > 259
    assert len(str(destination)) <= 259
    attempted = []
    original_replace = builtins.os.replace

    def replace(source, target):
        attempted.append(Path(source))
        return original_replace(source, target)

    monkeypatch.setattr(builtins.os, "replace", replace)
    builtins._atomic_write_text(destination, "research reference\r\n")
    assert len(attempted) == 1 and len(str(attempted[0])) <= 259
    assert destination.read_bytes() == b"research reference\n"
    assert list(parent.iterdir()) == [destination]


def test_temporary_collision_never_overwrites_or_deletes_another_writer(tmp_path, monkeypatch):
    identifier = UUID(int=4)
    monkeypatch.setattr(builtins.uuid, "uuid4", lambda: identifier)
    temporary = tmp_path / f".argus-{identifier.hex}.tmp"
    temporary.write_bytes(b"another writer's reference")
    destination = tmp_path / "seed.md"
    destination.write_bytes(b"existing user edits")
    with pytest.raises(FileExistsError):
        builtins._atomic_write_text(destination, "new reference")
    assert temporary.read_bytes() == b"another writer's reference"
    assert destination.read_bytes() == b"existing user edits"


def test_encoding_failure_preserves_original_and_cleans_owned_temporary(tmp_path):
    destination = tmp_path / "seed.md"
    destination.write_bytes(b"existing user edits")
    with pytest.raises(UnicodeEncodeError):
        builtins._atomic_write_text(destination, "\ud800")
    assert destination.read_bytes() == b"existing user edits"
    assert list(tmp_path.iterdir()) == [destination]
