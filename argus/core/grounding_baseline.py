"""What the workspace held when work on an objective began, by file facts.

A Reviewer may call a check impossible here only by quoting the task, its
packet, or the environment. An environment file counts only if it is exactly
as it was when the first mission on this objective started: its file facts
then are recorded once, here, and reused by every later mission on the
objective. So a note an Engineer wrote, in this mission or an earlier one on
the same objective, never counts, and neither does an edit to a file that was
there: an mtime can be set back, but the change time an edit leaves cannot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping

from .atomic_replace import write_atomic
from .file_facts import file_facts

FILENAME = "grounding-baseline.json"
#: Objectives remembered at once; the least recently recorded go first.
MAX_OBJECTIVES = 16
#: Bounds on one snapshot. A file outside them is simply not a source.
MAX_FILE_BYTES = 8_000_000
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 512_000_000
#: Directories that hold tooling or caches, never task or environment statements.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
})


def snapshot(roots: Iterable[Path | str]) -> dict[str, list[int]]:
    """Resolved path -> ``file_facts`` of the regular files under ``roots``, within the bounds above.

    Symbolic links are not followed; a link resolves to its target, which is
    recorded when it lies under a root.
    """
    facts: dict[str, list[int]] = {}
    total = 0
    for root in dict.fromkeys(str(root) for root in roots if root):
        try:
            base = Path(root).resolve()
        except (OSError, RuntimeError):
            continue
        if not base.is_dir():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
            for name in sorted(files):
                path = Path(directory) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    size = path.stat().st_size
                    if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                        continue
                    facts[str(path.resolve())] = file_facts(path)
                except (OSError, RuntimeError):
                    continue
                total += size
                if len(facts) >= MAX_FILES:
                    return facts
    return facts


def _key(objective: str, roots: Iterable[Path | str]) -> str:
    resolved = sorted({str(Path(root).resolve()) for root in roots if root})
    return json.dumps([str(objective or "").strip(), resolved], ensure_ascii=False)


def _read(root: Path) -> dict[str, dict[str, list[int]]]:
    """Recorded baselines; a version-1 file held digests and records none."""
    try:
        payload = json.loads((Path(root) / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("objectives") if isinstance(payload, dict) else None
    if not isinstance(rows, dict) or payload.get("version") != 2:
        return {}
    return {
        key: {str(path): list(value) for path, value in row.items() if isinstance(value, list)}
        for key, row in rows.items() if isinstance(row, dict)
    }


def _write(root: Path, rows: Mapping[str, Mapping[str, list[int]]]) -> None:
    path = Path(root) / FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps({"version": 2, "objectives": dict(rows)}, ensure_ascii=False))
    except OSError:
        # Unrecorded, the snapshot taken now still serves this mission.
        return


def objective_baseline(
    state_root: Path | str | None, objective: str, roots: Iterable[Path | str],
) -> dict[str, list[int]]:
    """The workspace as it was when the first mission on ``objective`` started.

    Recorded under ``state_root`` the first time and returned unchanged after.
    Without a state root or an objective, the workspace as it is now (this
    mission's start) is the baseline.
    """
    roots = tuple(str(root) for root in roots if root)
    if state_root is None or not str(objective or "").strip():
        return snapshot(roots)
    key = _key(objective, roots)
    rows = _read(Path(state_root))
    if key in rows:
        return rows[key]
    taken = snapshot(roots)
    rows[key] = taken
    _write(Path(state_root), dict(list(rows.items())[-MAX_OBJECTIVES:]))
    return taken


__all__ = ["FILENAME", "objective_baseline", "snapshot"]
