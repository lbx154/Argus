"""Persisted operator knob overrides (backend / model / reasoning-effort).

Every hyperparameter switch the operator makes today (``/backend``,
``/config``, or the natural-language recognizers in ``manager.repl`` —
"把模型换成 sonnet 5", "engineer 用 claude") only sets ``os.environ`` for the
CURRENT process. The running daemon is a separate process with its own
environment snapshot, and even the REPL itself forgets the switch the moment
it restarts — "一次改动以后都能读取" (change it once, have it read
consistently from then on) was not actually true.

This module is the single persisted layer under that expectation: a flat
``ARGUS_SKILL_*`` env-var-name -> value map at ``core.paths.config_path()``
(``~/.argus-skill/config.json``, resolved via ``ARGUS_SKILL_HOME`` like every
other cross-project file — never a hard-coded path). Every existing resolver
(``core.knobs.resolve_role_model``, ``cli.roles_status``'s backend/model
display resolution, the raw ``ARGUS_SKILL_RUNNER_BACKEND`` reads in
``apps._runtime``) is wired to check it as ONE MORE precedence layer:

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
from typing import Mapping

from .paths import config_path

log = logging.getLogger(__name__)

__all__ = ["read_persisted_knobs", "write_persisted_knob", "persisted_knob"]


def read_persisted_knobs() -> dict[str, str]:
    """Read the full persisted-knob map. Empty dict if missing/malformed —
    a corrupt or absent config.json must never break knob resolution."""
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.warning("knob_store: %s is not valid JSON — ignoring", path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}


def write_persisted_knob(name: str, value: str) -> None:
    """Atomically set ONE knob in the persisted map (read-modify-write; same
    write-to-temp + ``os.replace`` pattern as ``daemon.life_worker``'s
    ``write_continuous_config``, for the same crash-safety reason: a reader
    must never observe a half-written file)."""
    name = (name or "").strip()
    if not name:
        return
    path = config_path()
    data = read_persisted_knobs()
    data[name] = str(value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
    except OSError:
        log.warning("knob_store: failed to persist %s to %s", name, path)


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
