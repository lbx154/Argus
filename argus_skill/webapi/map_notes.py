"""Operator notes pinned to Atlas map nodes.

One note is one JSONL row — ``{id, node_id, text, author, ts}`` — appended to
``<life_dir>/map_notes.jsonl``. The file follows the backlog's per-session
state conventions: a sibling ``.lock`` file taken with ``portalocker`` plus a
process-local thread lock, and rows written through the same fsynced
``_append_jsonl`` helper, so concurrent web workers interleave whole rows.

The Planner reads the same file every cycle through the current-reality
digest (``life/supervisor/_planner_orchestration._operator_map_note_lines``);
keep the filename and row shape in step with that reader.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import portalocker

from ..life.memory import _append_jsonl, _read_jsonl_tail

NOTES_FILENAME = "map_notes.jsonl"
NOTE_NODE_ID_MAX_CHARS = 160
NOTE_TEXT_MAX_CHARS = 2000
NOTE_AUTHOR_MAX_CHARS = 80
LISTED_NOTES_LIMIT = 200

_NOTE_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_NOTE_THREAD_LOCKS_GUARD = threading.Lock()


def notes_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / NOTES_FILENAME


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.parent / f"{path.name}.lock"
    key = os.path.normcase(str(lock_path))
    with _NOTE_THREAD_LOCKS_GUARD:
        thread_lock = _NOTE_THREAD_LOCKS.setdefault(key, threading.Lock())
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        with lock_path.open("a+b") as handle:
            portalocker.lock(handle, portalocker.LOCK_EX)
            try:
                yield
            finally:
                portalocker.unlock(handle)


def append_note(
    life_dir: Path | str,
    *,
    node_id: str,
    text: str,
    author: str = "",
) -> dict[str, Any]:
    """Validate and durably append one note; returns the stored row."""
    node_id = str(node_id or "").strip()
    text = str(text or "").strip()
    author = str(author or "").strip()
    if not node_id or len(node_id) > NOTE_NODE_ID_MAX_CHARS:
        raise ValueError(
            f"a map note needs a node id of 1..{NOTE_NODE_ID_MAX_CHARS} characters"
        )
    # Node ids are backlog/team digests. Anything else — especially newlines —
    # would be rendered verbatim into the planner digest and could forge
    # digest lines the operator never wrote.
    if not all(ch.isalnum() or ch in ":_-." for ch in node_id):
        raise ValueError(
            "a map note node id may only contain letters, digits, ':', '_', '-', '.'"
        )
    if not text or len(text) > NOTE_TEXT_MAX_CHARS:
        raise ValueError(
            f"a map note needs text of 1..{NOTE_TEXT_MAX_CHARS} characters"
        )
    if len(author) > NOTE_AUTHOR_MAX_CHARS:
        raise ValueError(
            f"a map note author is limited to {NOTE_AUTHOR_MAX_CHARS} characters"
        )
    note = {
        "id": uuid.uuid4().hex[:12],
        "node_id": node_id,
        "text": text,
        "author": author,
        "ts": time.time(),
    }
    path = notes_path(life_dir)
    with _locked(path):
        _append_jsonl(path, [note])
    return note


def list_notes(
    life_dir: Path | str,
    limit: int = LISTED_NOTES_LIMIT,
) -> list[dict[str, Any]]:
    """The latest *limit* notes, oldest first; missing file means no notes."""
    path = notes_path(life_dir)
    try:
        if not path.is_file():
            return []
    except OSError:
        return []
    rows = _read_jsonl_tail(path, max(0, int(limit)))
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("node_id") or "").strip()
        and str(row.get("text") or "").strip()
    ]


__all__ = [
    "LISTED_NOTES_LIMIT",
    "NOTE_AUTHOR_MAX_CHARS",
    "NOTE_NODE_ID_MAX_CHARS",
    "NOTE_TEXT_MAX_CHARS",
    "NOTES_FILENAME",
    "append_note",
    "list_notes",
    "notes_path",
]
