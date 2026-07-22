"""Project garbage collection — prune stale per-project state under
``~/.argus-skill/projects/``.

Every distinct cwd/git-remote ever used by ``argus-skill`` leaves a
``projects/<fingerprint>/`` subtree (see :func:`argus_skill.core.project.
project_fingerprint`). Nothing ever removed them, so they accumulated
indefinitely (observed: ~960 dirs / 400 MB on a long-lived host).

This module adds a conservative, REVERSIBLE garbage collector:

* A project is removed ONLY when it is BOTH
  1. **not live** — its ``daemon.pid`` does not point at a running process
     (so a running daemon is never
     touched), and
  2. **stale** — nothing under it has been modified within
     ``retention_days``.
* Explicit GC can sweep empty-session litter after a grace period. Automatic
  startup GC never does this: multiple independently-run WebAPI instances may
  share one global root, so one developer's startup must not invalidate another
  developer's still-open idle TUI session.
* Removal is a **move to ``projects_trash/<date>/``**, never an ``rm`` —
  so an over-eager prune is fully recoverable (the operator has been
  bitten by irreversible deletes before).

Hook it at daemon startup (cheap, fail-soft) and expose it as
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
_DEFAULT_EMPTY_GRACE_SECONDS = 3600.0
_LOCK_FILES = ("daemon.pid",)
# Files whose mtime signals real activity in a project (appends bump the
# file mtime, not always the dir mtime, so we check them explicitly).
_ACTIVITY_FILES = (
    "events.jsonl",
    "backlog.jsonl",
    "daemon.status.json",
    "continuous.json",
    "transcript.jsonl",
    # An idle web session has no events/backlog yet. Its session metadata is the
    # only activity signal between creation and the operator's first message.
    "session.json",
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
    """True if a daemon for this project is currently running."""
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


def _project_is_empty(project_dir: Path) -> bool:
    """True if a project holds no real work — bare-launch litter.

    Empty = no backlog items, no events, no saved conversation, no
    named/objective session, no continuous objective. Such dirs are minted by
    every bare ``argus-skill`` launch (a fresh session) and accumulate fast.
    The caller applies an age grace before moving one; this predicate only
    describes content and must not decide startup liveness by itself.
    """
    import json

    for name in ("backlog.jsonl", "events.jsonl", "transcript.jsonl"):
        try:
            f = project_dir / name
            if f.exists() and f.stat().st_size > 2:
                return False
        except OSError:
            pass
    for fname, keys in (("session.json", ("display_name", "objective", "origin")),
                        ("continuous.json", ("objective",))):
        try:
            f = project_dir / fname
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                if any(str(data.get(k) or "").strip() for k in keys):
                    return False
        except Exception:  # noqa: BLE001
            pass
    return True


def gc_stale_projects(
    global_root: Path | None = None,
    *,
    retention_days: int | None = None,
    dry_run: bool = False,
    sweep_empty: bool = True,
    empty_grace_seconds: float = _DEFAULT_EMPTY_GRACE_SECONDS,
    now: float | None = None,
    exclude: set[str] | None = None,
) -> list[str]:
    """Move stale/empty, not-live project dirs to ``projects_trash/<date>/``.

    A project is pruned when it is not-live (no running daemon) AND either
    (a) untouched for ``retention_days``, or (b) ``sweep_empty`` and it is
    content-less litter older than ``empty_grace_seconds``. The grace period is
    load-bearing for concurrent Web/TUI startups: a fresh idle session exists
    briefly without a daemon, backlog, transcript, or event.
    Returns the fingerprints pruned (or that WOULD be, when ``dry_run``).

    ``exclude`` names project fingerprints that must NEVER be pruned. A startup
    sweep may run before the caller's own ``daemon.pid`` lock is
    written, so a just-resolved ``--resume <id>`` of a long-parked project is
    not-yet-live and would otherwise be trashed out from under the session that
    is resuming it. The caller passes its own session fingerprint here.

    Fail-soft: a bad single project never aborts the sweep.
    """
    if retention_days is None:
        retention_days = retention_days_default()
    exclude = exclude or set()
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400.0
    empty_cutoff = now - max(0.0, float(empty_grace_seconds))

    runtime_root = global_root or core_paths.global_root()
    root = core_paths.session_states_root(runtime_root)
    if not root.exists():
        return []

    pruned: list[str] = []
    trash_dir = core_paths.session_trash_root(runtime_root)
    date = time.strftime("%Y%m%d", time.localtime(now))

    for project_dir in sorted(root.iterdir()):
        try:
            if not project_dir.is_dir():
                continue
            if project_dir.name in exclude:
                continue  # the caller's own active session — never prune
            if _project_is_live(project_dir):
                continue
            last_active = _project_last_active(project_dir)
            empty = (
                sweep_empty
                and last_active < empty_cutoff
                and _project_is_empty(project_dir)
            )
            if not empty and last_active >= cutoff:
                continue  # not empty and too recent
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


def maybe_gc_stale_projects(
    global_root: Path | None = None, *, exclude: set[str] | None = None
) -> list[str]:
    """Startup-hook wrapper: run GC, swallow everything (never break boot).

    Startup GC deliberately leaves recent empty-session litter alone. A shared
    ``ARGUS_SKILL_HOME`` can be served by multiple independent WebAPI processes,
    none of which can know whether another process's idle session is still open
    in a TUI. The explicit ``--gc`` path retains the one-hour empty sweep.

    ``exclude`` is forwarded so a startup sweep never trashes the caller's own
    just-resolved (and not-yet-locked) session.
    """
    try:
        return gc_stale_projects(
            global_root,
            sweep_empty=False,
            exclude=exclude,
        )
    except Exception:  # noqa: BLE001 — GC is best-effort housekeeping
        log.exception("project-gc: sweep failed (ignored)")
        return []
