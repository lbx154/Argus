"""A workspace as plain, comparable facts: no file is read, nothing is hashed.

A git workspace is its HEAD tree plus ``[status, path, file_facts]`` for
every path git reports as changed; any other tree is ``[path, file_facts]``
for each of its files. Two snapshots describe the same workspace exactly when
they compare equal, and a diff of two snapshots names what moved.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .file_facts import file_facts

# Version control, caches, environments and Argus's own runtime state, at any
# depth: none of it is the work.
IGNORED_NAMES = frozenset({
    ".agents",
    ".argus",
    ".argus_external_work",
    ".argus_subagents",
    ".autors",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
})
# A git that has not answered by then (a stale lock, a hung mount) leaves the
# facts unknown; the work it describes goes on without them.
GIT_TIMEOUT_SECONDS = 10.0


def fact_text(value: Any) -> str:
    """``value`` as one canonical JSON string: equal facts give equal text."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )


def _relative(text: str) -> str:
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _skipped(relative: str, ignore: tuple[str, ...]) -> bool:
    parts = PurePosixPath(relative).parts
    if any(part in IGNORED_NAMES or part.endswith(".egg-info") for part in parts):
        return True
    return any(relative == path or relative.startswith(path + "/") for path in ignore)


def _facts(path: Path) -> list[int]:
    return file_facts(path) if path.is_file() else []


def _git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=check, capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
    ).stdout.decode("utf-8", errors="surrogateescape")


def _git_facts(root: Path, ignore: tuple[str, ...]) -> dict[str, Any]:
    records = [item for item in _git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=normal",
    ).split("\0") if item]
    changes: list[list[Any]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        status, relative = record[:2], _relative(record[3:])
        if "R" in status or "C" in status:
            index += 1  # the source path; the destination is what exists now
        if relative and not _skipped(relative, ignore):
            changes.append([status, relative, _facts(root / relative)])
    return {
        # The tree, not the commit: a history-only commit changes no work.
        "tree": _git(root, "rev-parse", "--verify", "-q", "HEAD^{tree}", check=False).strip(),
        "changes": sorted(changes, key=lambda row: row[1]),
    }


def _ignore(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(path for path in (_relative(str(value)) for value in paths) if path)


def tree_files(root: Path | str, *, ignore: Iterable[str] = ()) -> list[list[Any]]:
    """``[path, file_facts]`` for every file under ``root``, walking the tree."""
    root, skip = Path(root), _ignore(ignore)
    files: list[list[Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(
            name for name in dirnames if not _skipped((base / name).as_posix(), skip)
        )
        for name in sorted(filenames):
            relative = (base / name).as_posix()
            if not _skipped(relative, skip):
                files.append([relative, _facts(Path(dirpath) / name)])
    return files


def files_written_since(
    root: Path | str, since: float, *, limit: int = 15, budget: int = 20_000,
) -> tuple[list[tuple[str, float]], int, bool]:
    """Files under ``root`` modified at or after ``since``, newest first.

    Returns up to ``limit`` ``(relative path, mtime)`` pairs, how many such
    files the walk saw, and whether it stopped after ``budget`` entries
    (then the count is a lower bound). The runtime's own directories are left
    out, except ``.argus/evidence``, where the team keeps run outputs.
    """
    root = Path(root)
    found: list[tuple[str, float]] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).relative_to(root).as_posix()
        if base == ".argus":
            dirnames[:] = [name for name in dirnames if name == "evidence"]
            filenames = []
        else:
            dirnames[:] = [name for name in dirnames if (base == "." and name == ".argus") or not _skipped(name, ())]
        for name in filenames:
            seen += 1
            if seen > budget:
                found.sort(key=lambda row: -row[1])
                return found[:limit], len(found), True
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if mtime >= since:
                found.append(((Path(base) / name).as_posix() if base != "." else name, mtime))
    found.sort(key=lambda row: -row[1])
    return found[:limit], len(found), False


def workspace_facts(root: Path | str, *, ignore: Iterable[str] = ()) -> dict[str, Any]:
    """What ``root`` looks like now, leaving out ``ignore`` (relative files or dirs).

    Only ``stat`` is read, so a large dataset or checkpoint costs nothing
    extra. A workspace that cannot be read in time is ``{"unavailable": True}``:
    unknown, never evidence that nothing changed.
    """
    root = Path(root)
    try:
        if (root / ".git").exists():
            return _git_facts(root, _ignore(ignore))
        return {"files": tree_files(root, ignore=ignore)}
    except (OSError, subprocess.SubprocessError):
        return {"unavailable": True}


__all__ = ["IGNORED_NAMES", "fact_text", "files_written_since", "tree_files", "workspace_facts"]
