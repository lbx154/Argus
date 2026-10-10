"""What the workspace held when work on an objective began, by content hash.

A Reviewer may call a check impossible here only by quoting the task, its
packet, or the environment. An environment file counts only if it is exactly
as it was when the first mission on this objective started: its sha256 then
is recorded once, here, and reused by every later mission on the objective.
So a note an Engineer wrote, in this mission or an earlier one on the same
objective, never counts, and neither does an edit to a file that was there.
Content, not timestamps: an mtime (or, on Windows, a birth time) can be set.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Iterable, Mapping

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


def file_sha256(path: Path | str) -> str:
    """The hex sha256 of a file's content."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_sha256(value: object) -> str:
    """``sha256:<hex>`` or ``<hex>`` as bare lowercase hex, or "" if neither."""
    text = str(value or "").strip().lower()
    text = text.removeprefix("sha256:")
    return text if len(text) == 64 and all(char in "0123456789abcdef" for char in text) else ""


def snapshot(roots: Iterable[Path | str]) -> dict[str, str]:
    """Resolved path -> sha256 of the regular files under ``roots``, within the bounds above.

    Symbolic links are not followed; a link resolves to its target, which is
    recorded when it lies under a root.
    """
    hashes: dict[str, str] = {}
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
                    hashes[str(path.resolve())] = file_sha256(path)
                except (OSError, RuntimeError):
                    continue
                total += size
                if len(hashes) >= MAX_FILES:
                    return hashes
    return hashes


def _key(objective: str, roots: Iterable[Path | str]) -> str:
    resolved = sorted({str(Path(root).resolve()) for root in roots if root})
    text = json.dumps([str(objective or "").strip(), resolved], ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(root: Path) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads((Path(root) / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("objectives") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {
        key: {str(path): str(digest) for path, digest in row.items()}
        for key, row in rows.items() if isinstance(row, dict)
    }


def _write(root: Path, rows: Mapping[str, Mapping[str, str]]) -> None:
    path = Path(root) / FILENAME
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"version": 1, "objectives": dict(rows)}, handle, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        # Unrecorded, the snapshot taken now still serves this mission.
        return
    finally:
        temporary.unlink(missing_ok=True)


def _input_key(objective: str, root: Path | str) -> str:
    return _key(objective, ()) + ":input:" + str(Path(root).resolve())


def objective_baseline(
    state_root: Path | str | None, objective: str, roots: Iterable[Path | str],
    *, input_roots: Iterable[Path | str] = (),
) -> dict[str, str]:
    """The workspace as it was when the first mission on ``objective`` started.

    Recorded under ``state_root`` the first time and returned unchanged after.
    Without a state root or an objective, the workspace as it is now (this
    mission's start) is the baseline.

    ``input_roots`` are the task's own input directories outside the workspace
    (``core/task_inputs.py``). Each is snapshotted only together with a new
    workspace baseline: a directory first named by a later mission may already
    hold an earlier mission's edits, so it is readable but grounds nothing.
    """
    roots = tuple(str(root) for root in roots if root)
    inputs = tuple(dict.fromkeys(str(root) for root in input_roots if root))
    if state_root is None or not str(objective or "").strip():
        return {**snapshot(inputs), **snapshot(roots)}
    key = _key(objective, roots)
    rows = _read(Path(state_root))
    if key in rows:
        taken_inputs: dict[str, str] = {}
        for root in inputs:
            taken_inputs.update(rows.get(_input_key(objective, root), {}))
        return {**taken_inputs, **rows[key]}
    taken = snapshot(roots)
    taken_inputs = {}
    for root in inputs:
        row = snapshot((root,))
        rows[_input_key(objective, root)] = row
        taken_inputs.update(row)
    rows[key] = taken
    _write(Path(state_root), dict(list(rows.items())[-MAX_OBJECTIVES * (1 + len(inputs)):]))
    return {**taken_inputs, **taken}


__all__ = [
    "FILENAME", "file_sha256", "normalize_sha256", "objective_baseline", "snapshot",
]
