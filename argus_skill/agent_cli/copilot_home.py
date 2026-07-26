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

Pointing the workers at ``<ARGUS_SKILL_HOME>/copilot-home`` fixes both. Auth is
unaffected: the Copilot CLI does not keep credentials under ``COPILOT_HOME``
(verified by running it against an empty one), so this relocates working state
only.

An operator who sets ``COPILOT_HOME`` themselves is always obeyed — including
the private per-worktree home the self-maintenance sandbox sets up, which must
keep pointing at its own copy.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Mapping

from ..core.paths import global_root

log = logging.getLogger(__name__)

COPILOT_HOME_ENV = "COPILOT_HOME"
_COPILOT_HOME_DIR = "copilot-home"

# Behaviour lives in these; a home without them would silently run with Copilot
# defaults instead of the operator's settings.
_SEEDED_CONFIG_FILES = ("config.json", "settings.json", "permissions-config.json")


def argus_copilot_home(env: Mapping[str, str] | None = None) -> Path:
    """Path of the Argus-owned Copilot home, beside the rest of Argus state."""
    source = env if env is not None else os.environ
    configured = str(source.get("ARGUS_SKILL_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else global_root()
    return root / _COPILOT_HOME_DIR


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
    "COPILOT_HOME_ENV",
    "apply_copilot_home",
    "argus_copilot_home",
    "prepare_copilot_home",
]
