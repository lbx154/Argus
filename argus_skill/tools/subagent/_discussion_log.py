"""Discussion transcript persistence for the supervisor ↔ engineer protocol.

Owns: discussion.jsonl read/write (with POSIX advisory locks), transcript
rendering, and the co-located human-readable DISCUSSION.md mirror.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX advisory locks for safe concurrent appends to the
    # shared discussion transcript (engineer CLI + supervisor loop).
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

# Registry path helpers live in _registry; imports are safe because _registry
# only imports discussion mirroring lazily inside function bodies.
from ._registry import _task_log_dir


# ---------------------------------------------------------------------------
# Transcript paths
# ---------------------------------------------------------------------------

def _discussion_path(task_id: str) -> Path:
    """Where the supervisor<->engineer discussion transcript for a task lives."""
    return _task_log_dir(task_id) / "discussion.jsonl"


# ---------------------------------------------------------------------------
# Transcript read/write
# ---------------------------------------------------------------------------

def _append_discussion(task_id: str, role: str, message: str) -> Path:
    """Append one turn (role + message) to the shared discussion transcript."""
    path = _discussion_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "role": "supervisor" if role == "supervisor" else "engineer",
        "message": " ".join(str(message or "").split()),
    }
    line = json.dumps(entry) + "\n"
    with path.open("a") as f:
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        f.write(line)
        f.flush()
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    return path


def _reset_discussion(task_id: str) -> None:
    """Drop a stale transcript so a reused task-id starts each run clean."""
    path = _discussion_path(task_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _read_discussion(task_id: str) -> list[dict[str, Any]]:
    """Return all complete discussion turns, oldest first; skip a partial line."""
    path = _discussion_path(task_id)
    if not path.exists():
        return []
    turns: list[dict[str, Any]] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip a partial/garbled (e.g. mid-append) line
                if isinstance(rec, dict) and rec.get("message"):
                    turns.append(rec)
    except OSError:
        return []
    return turns


def _engineer_turn_count(task_id: str) -> int:
    """Count engineer turns so the supervisor can detect a new reply to answer."""
    return sum(1 for t in _read_discussion(task_id) if t.get("role") == "engineer")


def _render_discussion(task_id: str, max_chars: int = 2000) -> str:
    """Render the transcript, newest last, for a supervisor prompt."""
    rendered = [
        f"[{t.get('role', 'engineer')}] {str(t.get('message', '')).strip()}"
        for t in _read_discussion(task_id)
        if str(t.get("message", "")).strip()
    ]
    if not rendered:
        return ""
    return "\n".join(rendered)[-max_chars:]


# ---------------------------------------------------------------------------
# Human-readable markdown mirror
# ---------------------------------------------------------------------------

def _mirror_discussion_md(task_id: str, run_dir: str | None) -> None:
    """Re-render a human-readable ``DISCUSSION.md`` in the run dir from the
    canonical ``discussion.jsonl``. The jsonl stays the single source of truth
    (locking, turn counts); the markdown is an atomic full re-render co-located
    with the experiment so the engineer reads/participates in one obvious file.
    """
    if not run_dir:
        return
    turns = _read_discussion(task_id)
    if not turns:
        return
    lines = [f"# Supervisor / engineer discussion — {task_id}",
             "",
             "_Reply with_ "
             f"`python -m argus_skill.tools.subagent reply --task-id {task_id} "
             '--message "..."`. _The run stays stopped until the supervisor marks '
             'the concern resolved._',
             ""]
    for t in turns:
        role = t.get("role", "engineer")
        ts = t.get("ts")
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
        who = "🤖 supervisor" if role == "supervisor" else "🛠️ engineer"
        lines.append(f"### {who} — {when}".rstrip(" —"))
        lines.append("")
        lines.append(str(t.get("message", "")).strip())
        lines.append("")
    try:
        p = Path(run_dir) / "DISCUSSION.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass
