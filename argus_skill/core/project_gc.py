"""Project garbage collection — prune stale per-project state under
``~/.argus-skill/projects/``.

Every distinct cwd/git-remote ever used by ``argus-skill`` leaves a
``projects/<fingerprint>/`` subtree (see :func:`argus_skill.core.project.
project_fingerprint`). Nothing ever removed them, so they accumulated
indefinitely (observed: ~960 dirs / 400 MB on a long-lived host).

This module adds a conservative, REVERSIBLE garbage collector:

* A project is removed ONLY when it is BOTH
  1. **not live** — neither its ``daemon.pid`` nor its ``repl.pid``
     points at a running process (so a running daemon/REPL is never
     touched), and
  2. **stale** — nothing under it has been modified within
     ``retention_days``.
* Removal is a **move to ``projects_trash/<date>/``**, never an ``rm`` —
  so an over-eager prune is fully recoverable (the operator has been
  bitten by irreversible deletes before).

Hook it at daemon / REPL startup (cheap, fail-soft) and expose it as
``argus-skill --gc``.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from . import paths as core_paths
from .daemon_lock import is_pid_running, read_daemon_pid

log = logging.getLogger(__name__)

_DEFAULT_RETENTION_DAYS = 30
_LOCK_FILES = ("daemon.pid", "repl.pid")
# Files whose mtime signals real activity in a project (appends bump the
# file mtime, not always the dir mtime, so we check them explicitly).
_ACTIVITY_FILES = (
    "events.jsonl",
    "memory.jsonl",
    "backlog.jsonl",
    "daemon.status.json",
    "continuous.json",
)


def retention_days_default() -> int:
    """Retention window in days, overridable via env."""
    raw = os.environ.get("ARGUS_SKILL_PROJECT_RETENTION_DAYS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _DEFAULT_RETENTION_DAYS


def _project_is_live(project_dir: Path) -> bool:
    """True if a daemon or REPL for this project is currently running."""
    for lock_file in _LOCK_FILES:
        pid = read_daemon_pid(project_dir / lock_file)
        if pid is not None and is_pid_running(pid):
            return True
    return False


def _project_last_active(project_dir: Path) -> float:
    """Most-recent mtime across the dir + its activity files (epoch secs)."""
    newest = 0.0
    try:
        newest = project_dir.stat().st_mtime
    except OSError:
        return time.time()  # can't stat -> treat as fresh (never prune)
    for name in _ACTIVITY_FILES:
        try:
            newest = max(newest, (project_dir / name).stat().st_mtime)
        except OSError:
            continue
    return newest


def gc_stale_projects(
    global_root: Path | None = None,
    *,
    retention_days: int | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> list[str]:
    """Move stale, not-live project dirs to ``projects_trash/<date>/``.

    A project is pruned ONLY when it is both not-live (no running
    daemon/repl) AND untouched for ``retention_days``. Returns the list of
    fingerprints pruned (or that WOULD be pruned, when ``dry_run``).

    Fail-soft: a bad single project never aborts the sweep.
    """
    if retention_days is None:
        retention_days = retention_days_default()
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400.0

    root = (global_root or core_paths.global_root()) / "projects"
    if not root.exists():
        return []

    pruned: list[str] = []
    trash_dir = (global_root or core_paths.global_root()) / "projects_trash"
    date = time.strftime("%Y%m%d", time.localtime(now))

    for project_dir in sorted(root.iterdir()):
        try:
            if not project_dir.is_dir():
                continue
            if _project_is_live(project_dir):
                continue
            if _project_last_active(project_dir) >= cutoff:
                continue  # too recent
            pruned.append(project_dir.name)
            if dry_run:
                continue
            dest_parent = trash_dir / date
            dest_parent.mkdir(parents=True, exist_ok=True)
            dest = dest_parent / project_dir.name
            if dest.exists():
                dest = dest_parent / f"{project_dir.name}.{int(now)}"
            shutil.move(str(project_dir), str(dest))
        except OSError as exc:  # noqa: PERF203 — per-item fail-soft is the point
            log.warning("project-gc: skipped %s: %s", project_dir, exc)
            if project_dir.name in pruned and not dry_run:
                pruned.remove(project_dir.name)
            continue

    if pruned:
        log.info(
            "project-gc: %s %d stale project(s) (retention=%dd)%s",
            "would prune" if dry_run else "moved to trash",
            len(pruned),
            retention_days,
            "" if dry_run else f" -> {trash_dir / date}",
        )
    return pruned


def maybe_gc_stale_projects(global_root: Path | None = None) -> list[str]:
    """Startup-hook wrapper: run GC, swallow everything (never break boot)."""
    try:
        return gc_stale_projects(global_root)
    except Exception:  # noqa: BLE001 — GC is best-effort housekeeping
        log.exception("project-gc: sweep failed (ignored)")
        return []
