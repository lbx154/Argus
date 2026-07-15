"""Atomically persist small text-artifact checkpoints from stdin."""
from __future__ import annotations

import argparse
import errno
import os
import sys
import tempfile
from pathlib import Path

_UNSUPPORTED_DIRECTORY_FSYNC_ERRNOS = {errno.EINVAL}
_UNSUPPORTED_DIRECTORY_FSYNC_ERRNOS.update(
    value
    for name in ("ENOTSUP", "EOPNOTSUPP")
    if (value := getattr(errno, name, None)) is not None
)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DIRECTORY_FSYNC_ERRNOS:
                raise
    finally:
        os.close(fd)


def atomic_write_text(path: Path | str, text: str) -> None:
    """Replace a text artifact after syncing its complete sibling temp file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_append_text(path: Path | str, text: str) -> None:
    """Append via an atomic rewrite so interruption preserves the old artifact."""
    target = Path(path)
    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    atomic_write_text(target, previous + text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persist a small text-artifact checkpoint using file fsync, atomic "
            "replacement, and directory fsync."
        )
    )
    parser.add_argument("action", choices=("write", "append"))
    parser.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    text = sys.stdin.read()
    if not text:
        parser.error("artifact content must be provided on stdin")
    if args.action == "write":
        atomic_write_text(args.path, text)
    else:
        atomic_append_text(args.path, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["atomic_append_text", "atomic_write_text", "main"]
