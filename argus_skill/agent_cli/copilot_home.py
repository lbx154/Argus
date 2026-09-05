"""Give Argus's Copilot workers a home of their own.

The Copilot CLI keeps its whole working state — session transcripts, the
session-store database, logs — under ``COPILOT_HOME``, defaulting to
``~/.copilot``. That default is the operator's personal directory: the same one
their own ``copilot`` invocations and their editor use.

Argus runs many Copilot-backed roles concurrently and continuously, so with the
default every daemon, every mission, and every control-plane call writes there
too. On the host this was measured against, the operator's ``~/.copilot`` held
46,220 session directories and 47 GB, growing by ~115 sessions an hour, while
the Argus-owned home next to the rest of its state held 10. The operator's own
history is buried, and the growth lands on whichever filesystem ``$HOME`` is on
rather than the one chosen for Argus state.

Pointing the workers at ``<ARGUS_SKILL_HOME>/copilot-home`` fixes both. Recent
Copilot CLI releases also keep login tokens in ``config.json`` under that home,
so an empty or stale isolated home can make ordinary one-shot workers report
``No authentication information found`` while a warm ACP process using the
operator home still works. Preparation therefore mirrors only the small set of
authentication fields from the operator config; Argus-owned session state and
all unrelated config fields remain isolated.

An operator who sets ``COPILOT_HOME`` themselves is always obeyed — including
the private per-worktree home the self-maintenance sandbox sets up, which must
keep pointing at its own copy.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Mapping

from ..core.paths import global_root

log = logging.getLogger(__name__)

COPILOT_HOME_ENV = "COPILOT_HOME"
_COPILOT_HOME_DIR = "copilot-home"

# Behaviour lives in these; a home without them would silently run with Copilot
# defaults instead of the operator's settings.
_SEEDED_CONFIG_FILES = ("config.json", "settings.json", "permissions-config.json")
_AUTH_CONFIG_KEYS = ("copilotTokens", "authTokens", "loggedInUsers", "lastLoggedInUser")
_CONFIG_HEADER = (
    "// User settings belong in settings.json.\n"
    "// This file is managed automatically.\n"
)

# Relocating the working state bounds nothing on its own: at the observed ~115
# sessions/hour a 7x24 host writes roughly 2.6 GB a day, so an unpruned Argus
# home simply becomes the next 47 GB somewhere else. Sessions are per-turn
# scratch — only a recent one can still be resumed — so they get an age limit,
# and the sweep is throttled because it runs on the child-env path.
_RETENTION_DAYS_ENV = "ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS"
_DEFAULT_RETENTION_DAYS = 7.0
_SWEEP_INTERVAL_SECONDS = 3600.0
_SWEEP_STAMP = ".argus-last-sweep"


def argus_copilot_home(env: Mapping[str, str] | None = None) -> Path:
    """Path of the Argus-owned Copilot home, beside the rest of Argus state."""
    source = env if env is not None else os.environ
    configured = str(source.get("ARGUS_SKILL_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else global_root()
    return root / _COPILOT_HOME_DIR


def _retention_days(env: Mapping[str, str]) -> float:
    raw = str(env.get(_RETENTION_DAYS_ENV) or "").strip()
    if not raw:
        return _DEFAULT_RETENTION_DAYS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_RETENTION_DAYS


def prune_copilot_sessions(
    home: Path,
    *,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> int:
    """Delete session scratch older than the retention window. Returns the count.

    Only ever called on the Argus-owned home — the operator's ``~/.copilot`` is
    theirs and is never swept. Age is the directory's own mtime, so a session
    that is still being written to or was just resumed looks fresh and survives.

    Deleting a session directory is safe even though ``session-store.db`` keeps
    its row: verified by removing one from a scratch home and running the CLI
    again, which worked with the orphaned record still present. Setting the
    retention to ``0`` disables pruning.
    """
    source = env if env is not None else os.environ
    days = _retention_days(source)
    if days <= 0:
        return 0
    root = Path(home) / "session-state"
    if not root.is_dir():
        return 0

    cutoff = (now if now is not None else time.time()) - days * 86400.0
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry)
        except OSError:  # noqa: PERF203 — one undeletable session must not stop the sweep
            continue
        removed += 1
    if removed:
        log.info("copilot home: pruned %d session(s) older than %.1fd", removed, days)
    return removed


def _sweep_is_due(home: Path, now: float) -> bool:
    """True at most once per :data:`_SWEEP_INTERVAL_SECONDS`, and claim the slot.

    The stamp is written *before* the sweep so that concurrent workers — and
    there are many — do not all scan the directory at once.
    """
    stamp = Path(home) / _SWEEP_STAMP
    try:
        if stamp.exists() and now - stamp.stat().st_mtime < _SWEEP_INTERVAL_SECONDS:
            return False
        stamp.touch()
    except OSError:
        return False
    return True


def _read_managed_config(path: Path) -> dict[str, object] | None:
    """Read Copilot's JSON-with-leading-comments managed config."""
    try:
        raw = path.read_text(encoding="utf-8")
        payload = "\n".join(
            line for line in raw.splitlines()
            if not line.lstrip().startswith("//")
        ).strip()
        value = json.loads(payload or "{}")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_managed_config(path: Path, value: dict[str, object]) -> bool:
    """Atomically write a private Copilot managed config."""
    temp_name = ""
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_CONFIG_HEADER)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _sync_operator_auth(personal: Path, target: Path) -> bool:
    """Mirror login identity into the isolated home without copying state.

    Replace each auth field whole, including newer ``authTokens`` account maps.
    Merging would retain removed accounts or expired token metadata; a field
    absent from the operator config must also disappear from the isolated home.
    """
    source = _read_managed_config(personal)
    current = _read_managed_config(target)
    if source is None or current is None:
        return False
    updated = dict(current)
    for key in _AUTH_CONFIG_KEYS:
        if key in source:
            updated[key] = source[key]
        else:
            updated.pop(key, None)
    if updated == current:
        return False
    return _write_managed_config(target, updated)


def prepare_copilot_home(env: Mapping[str, str] | None = None) -> Path | None:
    """Create the Argus Copilot home and seed the operator's config into it.

    Returns the path, or ``None`` if it cannot be prepared — the caller then
    leaves ``COPILOT_HOME`` alone rather than pointing a worker at a directory
    that does not exist.
    """
    source = env if env is not None else os.environ
    home = argus_copilot_home(source)
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("copilot home unavailable at %s; using the default", home)
        return None

    personal = Path(str(source.get("HOME") or Path.home())) / ".copilot"
    for name in _SEEDED_CONFIG_FILES:
        target = home / name
        if target.exists():
            continue
        origin = personal / name
        if not origin.is_file():
            continue
        try:
            shutil.copy2(origin, target)
        except OSError:  # noqa: PERF203 — one bad file must not lose the rest
            log.warning("could not seed %s into the Argus copilot home", name)

    # Authentication moved into COPILOT_HOME/config.json in newer CLI builds.
    # Keep only those fields current: copying the whole operator config on every
    # turn would collapse the storage/state isolation this module provides.
    _sync_operator_auth(personal / "config.json", home / "config.json")

    now = time.time()
    if _sweep_is_due(home, now):
        prune_copilot_sessions(home, env=source, now=now)
    return home


def apply_copilot_home(env: dict[str, str]) -> dict[str, str]:
    """Point ``env`` at the Argus Copilot home unless one is already chosen.

    Mutates and returns ``env`` so it can be used inline while building a child
    environment.
    """
    if str(env.get(COPILOT_HOME_ENV) or "").strip():
        return env
    home = prepare_copilot_home(env)
    if home is not None:
        env[COPILOT_HOME_ENV] = str(home)
    return env


__all__ = [
    "prune_copilot_sessions",
    "COPILOT_HOME_ENV",
    "apply_copilot_home",
    "argus_copilot_home",
    "prepare_copilot_home",
]
