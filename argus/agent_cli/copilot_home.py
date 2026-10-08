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

Without a dedicated account binding, an operator's explicit ``COPILOT_HOME``
is always obeyed, including self-maintenance's private per-worktree home.

An explicit ``ARGUS_SKILL_COPILOT_HOME`` account binding takes precedence over
the caller's Copilot environment. Its credentials are owned by Copilot login,
never seeded, synchronized, or pruned by Argus.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

import portalocker

from ..core.paths import global_root
from ..core.trace_archive import (
    archive_trace,
    reject_links,
    restore_trace,
    sync_directory,
    trace_snapshot,
)

log = logging.getLogger(__name__)

COPILOT_HOME_ENV = "COPILOT_HOME"
COPILOT_ACCOUNT_HOME_KNOB = "ARGUS_SKILL_COPILOT_HOME"
_COPILOT_HOME_DIR = "copilot-home"

# Behaviour lives in these; a home without them would silently run with Copilot
# defaults instead of the operator's settings.
_SEEDED_CONFIG_FILES = ("config.json", "settings.json", "permissions-config.json")
_AUTH_CONFIG_KEYS = ("copilotTokens", "authTokens", "loggedInUsers", "lastLoggedInUser")
_CONFIG_HEADER = (
    "// User settings belong in settings.json.\n"
    "// This file is managed automatically.\n"
)

# Raw sessions are research evidence. Preserve them by default; an operator
# may opt into verified archival and reclamation after an inactivity window.
_RETENTION_DAYS_ENV = "ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS"
_DEFAULT_RETENTION_DAYS = 0.0
_SWEEP_INTERVAL_SECONDS = 3600.0
_SWEEP_STAMP = ".argus-last-sweep"
_USE_LOCK = ".argus-session-use.lock"


def copilot_account_home(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the opt-in account binding without touching credentials."""
    from ..core.knob_store import persisted_knob
    from ..core.paths import resolve_runtime_path
    from ..trial.client import trial_enabled

    source = os.environ if env is None else env
    if trial_enabled(source):
        return None
    # An explicitly empty value lets setup validate removal before persisting it.
    raw = (
        source[COPILOT_ACCOUNT_HOME_KNOB]
        if COPILOT_ACCOUNT_HOME_KNOB in source
        else persisted_knob(COPILOT_ACCOUNT_HOME_KNOB, env=source)
    )
    if not raw.strip():
        return None
    return resolve_runtime_path(raw.strip(), context=COPILOT_ACCOUNT_HOME_KNOB).resolve()


def apply_copilot_account(env: dict[str, str]) -> dict[str, str]:
    """Bind Copilot children to the selected account, not ambient credentials."""
    home = copilot_account_home(env)
    if home is None:
        return env
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise RuntimeError(f"Cannot use dedicated Copilot home {home}: {exc}") from exc
    for key in tuple(env):
        if key.startswith("COPILOT_PROVIDER_") or key in {
            "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_OFFLINE",
        }:
            env.pop(key, None)
    env[COPILOT_HOME_ENV] = str(home)
    # An unlogged-in profile must not silently fall back to the operator's gh.
    env["GH_CONFIG_DIR"] = str(home / "gh")
    return env


def argus_copilot_home(env: Mapping[str, str] | None = None) -> Path:
    """Path of the Argus-owned Copilot home, beside the rest of Argus state."""
    source = env if env is not None else os.environ
    configured = str(source.get("ARGUS_SKILL_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else global_root()
    return root / _COPILOT_HOME_DIR


def _retention_days(env: Mapping[str, str]) -> float:
    from ..core.knob_store import persisted_knob

    raw = persisted_knob(_RETENTION_DAYS_ENV, env=env).strip()
    if not raw:
        return _DEFAULT_RETENTION_DAYS
    try:
        days = float(raw)
        if not math.isfinite(days) or days < 0:
            raise ValueError(raw)
        return days
    except ValueError:
        log.warning("Invalid Copilot trace retention %r; preserving sessions", raw)
        return _DEFAULT_RETENTION_DAYS


@contextmanager
def _home_lock(home: Path, flags):
    # A linked state root is valid for normal workers. Lock its actual home;
    # archival still rejects the original linked path before reclamation.
    home = home.resolve()
    reject_links(home)
    path = home / _USE_LOCK
    if path.exists():
        reject_links(path)
    with path.open("a+b") as handle:
        portalocker.lock(handle, flags)
        try:
            yield
        finally:
            portalocker.unlock(handle)


def _session_path(home: Path, session_id: str) -> Path:
    if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id or "\\" in session_id or "/" in session_id:
        raise ValueError("invalid Copilot session id")
    root = home / "session-state"
    reject_links(home)
    if root.exists():
        reject_links(root)
    return root / session_id


def restore_copilot_session(home: Path, session_id: str) -> bool:
    """Restore an archived session before resume; caller holds the use lock."""
    target = _session_path(home, session_id)
    archives = home / "session-archives" / session_id
    if not archives.is_dir():
        return False
    reject_links(archives)
    target.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(archives / ".restore.lock", mode="a", flags=portalocker.LOCK_EX):
        marker = archives / ".reclaim-in-progress"
        if target.exists() and not marker.exists():
            reject_links(target)
            return False
        if marker.exists():
            reject_links(marker)
            name = marker.read_text(encoding="utf-8").strip()
            if Path(name).name != name or not name.endswith(".zip"):
                raise OSError("invalid incomplete reclamation marker")
            candidates = [archives / name]
        else:
            candidates = sorted(archives.glob("*.zip"), reverse=True)
        if not candidates:
            if marker.exists():
                raise OSError("incomplete Copilot reclamation has no archive")
            return False
        reject_links(candidates[0])
        if target.exists():
            reject_links(target)
            # Interrupted rmtree may leave a partial directory. Keep that
            # remainder too, and restore the verified complete snapshot.
            target.rename(archives / f"partial-{uuid.uuid4().hex}")
            sync_directory(target.parent)
            sync_directory(archives)
        restore_trace(candidates[0], target)
        marker.unlink(missing_ok=True)
        sync_directory(archives)
    log.info("Restored archived Copilot session %s", session_id)
    return True


@contextmanager
def copilot_session_use(env: Mapping[str, str] | None = None, *, session_id: str | None = None):
    """Protect the whole lifetime of a worker using the automatically managed home."""
    source = os.environ if env is None else env
    home = argus_copilot_home(source)
    configured = copilot_account_home(source)
    if configured is None and str(source.get(COPILOT_HOME_ENV) or "").strip():
        configured = Path(source[COPILOT_HOME_ENV]).expanduser()
    if configured is not None and configured.resolve() != home.resolve():
        yield False
        return
    home.mkdir(parents=True, exist_ok=True)
    # Reclaim only while idle. A resumed session is protected even before its
    # process has started; touching it closes the gap before the shared lock.
    if configured is None:
        _sweep_home(home, source, protected_session_id=session_id)
    with _home_lock(home, portalocker.LOCK_SH):
        if session_id:
            restore_copilot_session(home.resolve(), session_id)
        yield True


def prune_copilot_sessions(
    home: Path,
    *,
    env: Mapping[str, str] | None = None,
    now: float | None = None,
) -> int:
    """Archive inactive sessions before reclaiming them; default 0 preserves all.

    Called only on the automatically managed home. Any live worker's shared
    use lock prevents reclamation, including workers currently producing no
    output. Archives are never expired automatically and are restored on resume.
    """
    source = env if env is not None else os.environ
    if not home.is_dir():
        return 0
    try:
        with _home_lock(home, portalocker.LOCK_EX | portalocker.LOCK_NB):
            return _prune_sessions_locked(home, source, now=now)
    except (OSError, portalocker.exceptions.LockException):
        return 0


def _prune_sessions_locked(home: Path, source: Mapping[str, str], *, now=None, protected_session_id=None) -> int:
    days = _retention_days(source)
    if days <= 0:
        return 0
    reject_links(home)
    root = Path(home) / "session-state"
    if not root.is_dir():
        return 0

    reject_links(root)
    cutoff = (now if now is not None else time.time()) - days * 86400.0
    removed = 0
    for entry in root.iterdir():
        if entry.name == protected_session_id or not entry.is_dir():
            continue
        try:
            if (home / "session-archives" / entry.name / ".reclaim-in-progress").exists():
                continue
            snapshot = trace_snapshot(entry)
            if max(row[3] for row in snapshot) / 1e9 >= cutoff:
                continue
            archived = archive_trace(entry, home / "session-archives" / entry.name)
            if trace_snapshot(entry) != archived.snapshot:
                continue
            if entry.resolve().parent != root.resolve():
                continue
            marker = archived.path.parent / ".reclaim-in-progress"
            with marker.open("w", encoding="utf-8") as handle:
                handle.write(archived.path.name)
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(marker.parent)
            shutil.rmtree(entry)
            sync_directory(root)
            marker.unlink(missing_ok=True)
            sync_directory(marker.parent)
        except (OSError, ValueError, zipfile.BadZipFile):  # one bad archive must not lose evidence
            log.warning("Could not archive Copilot session %s; preserving it", entry.name, exc_info=True)
            continue
        removed += 1
    if removed:
        log.info("copilot home: archived and reclaimed %d session(s) inactive for %.1fd", removed, days)
    return removed


def _sweep_home(home: Path, env: Mapping[str, str], *, protected_session_id=None) -> None:
    try:
        with _home_lock(home, portalocker.LOCK_EX | portalocker.LOCK_NB):
            if protected_session_id:
                target = _session_path(home.resolve(), protected_session_id)
                if target.is_dir():
                    reject_links(target)
                    target.touch()
            now = time.time()
            if _retention_days(env) > 0 and _sweep_is_due(home, now):
                _prune_sessions_locked(home, env, now=now, protected_session_id=protected_session_id)
    except (OSError, portalocker.exceptions.LockException):
        return


def _sweep_is_due(home: Path, now: float) -> bool:
    """True at most once per :data:`_SWEEP_INTERVAL_SECONDS`, and claim the slot.

    The caller holds the exclusive use lock, serializing concurrent sweepers.
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

    _sweep_home(home, source)
    notice = home / ".argus-trace-policy-v1"
    try:
        with notice.open("x", encoding="utf-8") as handle:
            handle.write("Session traces are preserved by default; opt-in reclamation archives first.\n")
        log.warning(
            "Copilot traces: automatic session reclamation is disabled by default; "
            "positive %s archives inactive sessions before reclaiming them. "
            "Archives in %s are kept until you remove them; monitor disk space.",
            _RETENTION_DAYS_ENV, home / "session-archives",
        )
    except OSError:
        pass
    return home


def copilot_log_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where the Copilot CLI writes its own log: the one place left to look
    when it exits without printing anything on stderr."""
    source = env if env is not None else os.environ
    configured = str(source.get(COPILOT_HOME_ENV) or "").strip()
    home = copilot_account_home(source)
    if home is None:
        home = Path(configured).expanduser() if configured else argus_copilot_home(source)
    return home / "logs"


def copilot_runtime_redactions() -> tuple[str, ...]:
    """Keep hosted-provider redactions behind the existing Copilot boundary."""
    from ..trial.client import runtime_redactions

    return runtime_redactions()


def copilot_uses_metered_provider() -> bool:
    """Whether replaying a failed Copilot transport could double-charge a turn."""
    from ..trial.client import trial_enabled

    return trial_enabled()


def apply_copilot_provider(env: dict[str, str]) -> None:
    """Reapply only the explicitly selected provider to an isolated child."""
    from ..trial.client import apply_trial_provider

    apply_trial_provider(env)


def apply_copilot_home(env: dict[str, str]) -> dict[str, str]:
    """Point ``env`` at the Argus Copilot home unless one is already chosen.

    Mutates and returns ``env`` so it can be used inline while building a child
    environment.
    """
    apply_copilot_provider(env)
    apply_copilot_account(env)
    if str(env.get(COPILOT_HOME_ENV) or "").strip():
        return env
    home = prepare_copilot_home(env)
    if home is not None:
        env[COPILOT_HOME_ENV] = str(home)
    return env


__all__ = [
    "prune_copilot_sessions",
    "copilot_session_use",
    "restore_copilot_session",
    "COPILOT_HOME_ENV",
    "COPILOT_ACCOUNT_HOME_KNOB",
    "copilot_account_home",
    "apply_copilot_account",
    "apply_copilot_home",
    "apply_copilot_provider",
    "copilot_runtime_redactions",
    "copilot_uses_metered_provider",
    "copilot_log_dir",
    "argus_copilot_home",
    "prepare_copilot_home",
]
