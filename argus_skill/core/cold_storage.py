"""Lossless gzip compaction for append-only markdown audit histories."""

from __future__ import annotations

import gzip
import os
import re
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

try:  # pragma: no cover - production is POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _directory_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".cold-storage.lock"
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _compress_group(
    directory: Path,
    paths: list[Path],
    *,
    keep_hot: int,
    sort_key: Callable[[Path], object],
) -> list[Path]:
    compressed: list[Path] = []
    with _directory_lock(directory):
        active = [path for path in paths if path.is_file()]
        active.sort(key=sort_key)
        cold = active if keep_hot <= 0 else active[:-keep_hot]
        for source in cold:
            target = source.with_suffix(source.suffix + ".gz")
            try:
                data = source.read_bytes()
                if target.exists():
                    if gzip.decompress(target.read_bytes()) == data:
                        source.unlink()
                        compressed.append(target)
                    continue
                payload = gzip.compress(data, compresslevel=9, mtime=0)
                temp = target.with_name(
                    f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
                )
                try:
                    temp.write_bytes(payload)
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
                source.unlink()
                compressed.append(target)
            except (OSError, EOFError, gzip.BadGzipFile):
                continue
    return compressed


def compact_skill_histories(skills_dir: Path | str, *, keep_hot: int) -> list[Path]:
    root = Path(skills_dir)
    compressed: list[Path] = []
    for history_root in root.rglob("_history") if root.is_dir() else []:
        for skill_dir in (path for path in history_root.iterdir() if path.is_dir()):
            versions = list(skill_dir.glob("v*.md"))

            def _version(path: Path) -> tuple[int, str]:
                match = re.fullmatch(r"v(\d+)\.md", path.name)
                return (int(match.group(1)) if match else 0, path.name)

            compressed.extend(
                _compress_group(
                    skill_dir,
                    versions,
                    keep_hot=keep_hot,
                    sort_key=_version,
                )
            )
    return compressed


def compact_wiki_retired(wiki_root: Path | str, *, keep_hot: int) -> list[Path]:
    retired_root = Path(wiki_root) / "pages" / "_retired"
    if not retired_root.is_dir():
        return []
    compressed: list[Path] = []
    for card_dir in (path for path in retired_root.iterdir() if path.is_dir()):
        groups: dict[str, list[Path]] = defaultdict(list)
        for path in card_dir.glob("*.md"):
            match = re.fullmatch(r"(.+?)(?:\.(\d+))?\.md", path.name)
            if match:
                groups[match.group(1)].append(path)
        for paths in groups.values():
            def _retirement_index(path: Path) -> tuple[int, str]:
                match = re.fullmatch(r".+?(?:\.(\d+))?\.md", path.name)
                return (int(match.group(1) or 1) if match else 0, path.name)

            compressed.extend(
                _compress_group(
                    card_dir,
                    paths,
                    keep_hot=keep_hot,
                    sort_key=_retirement_index,
                )
            )
    return compressed


def cold_storage_stats(paths: list[Path]) -> dict[str, int]:
    """Return lossless compression byte accounting for newly-created gzip files."""
    before = 0
    after = 0
    for path in paths:
        try:
            payload = path.read_bytes()
            after += len(payload)
            before += len(gzip.decompress(payload))
        except (OSError, EOFError, gzip.BadGzipFile):
            continue
    return {
        "bytes_before": before,
        "bytes_after": after,
        "bytes_saved": max(0, before - after),
    }


__all__ = ["cold_storage_stats", "compact_skill_histories", "compact_wiki_retired"]
