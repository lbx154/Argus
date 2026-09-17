"""Persisted operator knob overrides (backend / model / reasoning-effort).

Every hyperparameter switch the operator makes today (``/backend``,
``/config``, or the natural-language recognizers in ``manager.config_intent`` —
"把模型换成 sonnet 5", "engineer 用 claude") only sets ``os.environ`` for the
CURRENT process. The running daemon is a separate process with its own
environment snapshot, and even the cockpit process forgets the switch the moment
it restarts — "一次改动以后都能读取" (change it once, have it read
consistently from then on) was not actually true.

This module is the persisted layer for non-project operator switches: a flat
``ARGUS_SKILL_*`` env-var-name -> value map at ``core.paths.config_path()``
(``~/.argus-skill/config.json``, resolved via ``ARGUS_SKILL_HOME`` like every
other cross-project file — never a hard-coded path). Every existing resolver
(``core.knobs.resolve_role_model``, ``core.role_config``'s backend/model
display resolution, the raw ``ARGUS_SKILL_RUNNER_BACKEND`` reads in
``apps._runtime``) is wired to check it as ONE MORE precedence layer. The sole
host-global USD budget is stored here too; projects have no separate budgets.

For the remaining knobs the precedence is:

    role-specific env override (this process, explicit)
        -> shared env override (this process, explicit)
        -> persisted value (this file — the "changed it once" layer)
        -> hard-coded / vault default

An explicit env var for THIS process always wins over a previously-persisted
switch — a deliberate, one-off override (a shell script, a CI job, a Docker
``-e`` flag) should never be silently shadowed by something the operator said
in chat last week. The persisted file only fills in when the process has no
env var at all for that knob.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

import portalocker

from .paths import config_path

log = logging.getLogger(__name__)

__all__ = [
    "KnobStoreCorruptError",
    "read_persisted_knobs",
    "write_persisted_knob",
    "write_persisted_knobs",
    "persisted_knob",
]


class KnobStoreCorruptError(RuntimeError):
    """``config.json`` exists but does not hold a knob map.

    The caller is expected to surface this to the operator, not to substitute
    an empty map. Every resolver in this codebase treats "no persisted knob"
    as "use the hard-coded default", so swallowing a parse failure silently
    reverts EVERY persisted operator switch at once — the backend of every
    role, the model of every route, the budget cap — with nothing in any
    artifact to say it happened. The fix is to repair or delete
    ``core.paths.config_path()``; a missing file is normal and still yields an
    empty map.
    """


_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _write_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            portalocker.lock(fd, portalocker.LOCK_EX)
            yield
        finally:
            try:
                portalocker.unlock(fd)
            except (OSError, portalocker.exceptions.LockException):
                pass
            os.close(fd)


def read_persisted_knobs() -> dict[str, str]:
    """Read the full persisted-knob map.

    Empty dict when the file is absent or unreadable — that is the normal
    "operator has never persisted a switch" state, and it correctly means
    "use the hard-coded defaults".

    A file that EXISTS but does not parse as a JSON object raises
    :class:`KnobStoreCorruptError`. It used to log a warning and return ``{}``,
    which is indistinguishable from "nothing persisted": every role silently
    dropped to its codex/default backend and every persisted model choice
    vanished, with the only trace a warning in a log nobody reads.
    """
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise KnobStoreCorruptError(
            f"persisted knob store {path} is not valid JSON: {exc}. "
            f"Every persisted operator switch (role backends, models, budget "
            f"cap) is unreadable until it is repaired or deleted."
        ) from exc
    if not isinstance(data, dict):
        # Valid JSON of the wrong shape is the same failure with a quieter
        # symptom — it used to return {} without even a warning.
        raise KnobStoreCorruptError(
            f"persisted knob store {path} holds a JSON "
            f"{type(data).__name__}, expected an object mapping "
            f"ARGUS_SKILL_* names to values. Repair or delete it."
        )
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}


def write_persisted_knob(name: str, value: str) -> bool:
    """Atomically set ONE knob in the persisted map (read-modify-write; same
    write-to-temp + ``os.replace`` pattern as ``daemon.life_worker``'s
    ``write_continuous_config``, for the same crash-safety reason: a reader
    must never observe a half-written file)."""
    name = (name or "").strip()
    if not name:
        return False
    return write_persisted_knobs({name: str(value)})


def write_persisted_knobs(values: Mapping[str, str]) -> bool:
    """Atomically validate-at-caller and persist a batch of knob overrides."""
    updates = {
        str(name).strip(): str(value)
        for name, value in values.items()
        if str(name).strip()
    }
    if not updates:
        return True
    path = config_path()
    try:
        with _write_lock(path):
            # A corrupt store raises KnobStoreCorruptError out of this
            # function. Deliberate: the read-modify-write below would
            # otherwise replace the operator's unparseable file with a map
            # holding only `updates`, silently discarding every other switch
            # it contained. `except OSError` below does not catch it.
            data = read_persisted_knobs()
            from ..agent_cli.runner_backend import normalize_runner_backend

            combined = {**data, **updates}
            shared_before = str(
                data.get("ARGUS_SKILL_RUNNER_BACKEND")
                or data.get("ARGUS_SKILL_LIFE_BACKEND")
                or ""
            ).strip()
            shared_after = str(
                combined.get("ARGUS_SKILL_RUNNER_BACKEND")
                or combined.get("ARGUS_SKILL_LIFE_BACKEND")
                or ""
            ).strip()
            for runner_bin_name, runner_bin in data.items():
                if (
                    not runner_bin
                    or runner_bin_name in updates
                    or (
                        runner_bin_name != "ARGUS_SKILL_RUNNER_BIN"
                        and not runner_bin_name.endswith("_RUNNER_BIN")
                    )
                ):
                    continue
                if runner_bin_name == "ARGUS_SKILL_RUNNER_BIN":
                    before, after = shared_before, shared_after
                else:
                    backend_name = (
                        f"{runner_bin_name.removesuffix('_RUNNER_BIN')}_BACKEND"
                    )
                    before = str(data.get(backend_name) or shared_before).strip()
                    after = str(combined.get(backend_name) or shared_after).strip()
                if (
                    before
                    and after
                    and normalize_runner_backend(before)
                    != normalize_runner_backend(after)
                ):
                    updates[runner_bin_name] = ""
            data.update(updates)
            fd, tmp_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.warning(
            "knob_store: failed to persist %d setting(s) to %s",
            len(updates),
            path,
        )
        return False


def persisted_knob(name: str, *, env: Mapping[str, str] | None = None) -> str:
    """Best-effort helper for the common call shape: current-process env
    first, then the persisted file, else "". Callers that need a specific
    layered precedence beyond this (e.g. role-specific THEN shared env
    before falling back here) should call ``read_persisted_knobs()``
    directly instead — this is the single-knob convenience wrapper for
    call sites that only had a single env var to check anyway."""
    env_map = env if env is not None else os.environ
    val = str(env_map.get(name, "") or "").strip()
    if val:
        return val
    return read_persisted_knobs().get(name, "")
