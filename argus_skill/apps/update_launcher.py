"""Temporarily move Windows console launchers out of an installer's way.

Windows permits renaming a running console launcher but does not permit pip or
uv to delete it. Keep the update synchronous while allowing the installer to
write new launchers at their original paths.
"""

from __future__ import annotations

import gc
import importlib
import os
import sys
import sysconfig
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_IS_WINDOWS = os.name == "nt"
_LAUNCHER_NAMES = ("argus", "argus-skill", "argus-doctor", "argus-plugin-server")


def _invoked_launcher_directory() -> Path | None:
    # uv may expose a copy/hard link outside the managed environment. The
    # distlib launcher also strips .exe from argv[0] before calling main().
    invoked = Path(sys.argv[0]).expanduser()
    if invoked.name.lower().removesuffix(".exe") in _LAUNCHER_NAMES:
        return invoked.resolve().parent
    return None


def _launcher_directories() -> set[Path]:
    directories = {Path(sysconfig.get_path("scripts")).resolve()}
    invoked = _invoked_launcher_directory()
    if invoked is not None:
        directories.add(invoked)
    return directories


def _cleanup_backups(launcher: Path) -> None:
    for backup in launcher.parent.glob(f".{launcher.name}.argus-update-*.bak"):
        try:
            backup.unlink()
        except OSError:
            # An earlier updater or backend may still be executing this image.
            # Its backup is removable after that process exits.
            pass


def _restore_launchers(moved: list[tuple[Path, Path]]) -> None:
    failures = []
    for launcher, backup in reversed(moved):
        try:
            launcher.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(launcher)
        except OSError as exc:
            failures.append(f"{backup} -> {launcher}: {exc}")
    if failures:
        raise OSError("could not restore Argus launcher backup(s): " + "; ".join(failures))


@contextmanager
def preserve_windows_launchers(
    *, scripts_directory: Path | None = None,
) -> Iterator[None]:
    """Allow an in-process update to replace its Windows entry points.

    Call immediately around the actual pip/uv installation, after validating
    the update source. On failure the original launchers are restored. Backups
    still locked by running processes are retained for the next update.
    Set scripts_directory for a pip --user installation so the system-wide
    installation is left alone.
    """
    if not _IS_WINDOWS or getattr(sys, "frozen", False):
        yield
        return

    moved: list[tuple[Path, Path]] = []
    try:
        # importlib.metadata scans the console .exe's appended ZIP because it
        # is on sys.path. Its cached ZipFile also holds a Windows file handle;
        # dropping import caches and their cycles releases that extra lock.
        importlib.invalidate_caches()
        gc.collect()
        if scripts_directory is None:
            directories = _launcher_directories()
        else:
            directories = {scripts_directory.resolve()}
            invoked = _invoked_launcher_directory()
            if invoked is not None:
                directories.add(invoked)
        for directory in sorted(directories):
            for name in _LAUNCHER_NAMES:
                launcher = directory / f"{name}.exe"
                _cleanup_backups(launcher)
                if not launcher.is_file():
                    continue
                backup = directory / f".{launcher.name}.argus-update-{uuid.uuid4().hex}.bak"
                launcher.rename(backup)
                moved.append((launcher, backup))
        yield
    except BaseException as exc:
        try:
            _restore_launchers(moved)
        except OSError as restore_error:
            # Preserve the installer error and its type even if recovery also
            # fails. Replacing it with a missing-backup error hides the cause.
            exc.add_note(str(restore_error))
        raise
    else:
        for launcher, backup in moved:
            if not launcher.exists():
                # An installer may preserve an existing entry point instead
                # of recreating it. Do not leave that command missing.
                launcher.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(launcher)
            else:
                try:
                    backup.unlink()
                except OSError:
                    pass


__all__ = ["preserve_windows_launchers"]
