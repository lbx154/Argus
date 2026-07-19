"""Centralised on-disk path layout for the unified argus-skill agent.

Single source of truth for everything under ``~/.argus-skill/``. All
runtime code that needs to read or write any agent state MUST go
through these helpers — never hard-code paths elsewhere.

Current runtime layout::

    ~/.argus-skill/
    ├─ identity.md
    ├─ global_budget.json
    ├─ tools/
    ├─ skills/
    │   └─ *_archive/
    └─ projects/
        └─ <fingerprint>/
            ├─ events.jsonl
            ├─ backlog.jsonl
            ├─ budget.json
            ├─ skills/
            ├─ continuous.json
            ├─ events.jsonl
            ├─ inbox.jsonl
            ├─ daemon.pid
            ├─ daemon.status.json

The global root holds only cross-project *identity* and shared skills. The
per-project ``events.jsonl`` is the canonical timeline; project journal data is
stored there as typed events with a ``journal_kind`` field. ``journal_path`` is retained
only for legacy single-project tooling.

Legacy compatibility helpers kept for older tests / tooling:

* ``bus/commands.jsonl`` / ``bus/outbox.jsonl`` / ``bus/status.json``
  / ``bus/daemon.pid`` — bus-era queue state, retained only for
  historical callers.
* ``projects/<fingerprint>/missions/<mission_id>/`` — historical
  mission-record helper, no longer part of the live cockpit surface.

Environment overrides:

* ``ARGUS_SKILL_HOME`` — overrides the global root entirely (highest
  priority; used by tests and multi-tenant setups).
* ``ARGUS_SKILL_LIFE_DIR`` — legacy, points at the old life root. We
  honour it as a compatibility shim by treating it as ``ARGUS_SKILL_HOME``
  IF the new var is not set, but log a deprecation hint at the call
  site that reads it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = [
    "global_root",
    "identity_path",
    "config_path",
    "journal_path",
    "bus_root",
    "commands_path",
    "outbox_path",
    "status_path",
    "daemon_pid_path",
    "skills_global_root",
    "skills_archive_root",
    "tools_root",
    "projects_root",
    "project_root",
    "project_memory_path",
    "project_backlog_path",
    "project_skills_root",
    "project_missions_root",
    "mission_root",
    "PathResolutionError",
    "resolve_runtime_path",
    "ensure_dir",
]

_PATH_PLACEHOLDER_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


class PathResolutionError(ValueError):
    """Raised when a runtime path contains an unresolved shell placeholder."""


def resolve_runtime_path(raw: str | Path, *, context: str) -> Path:
    """Expand shell variables and ``~`` in a runtime path.

    ``os.path.expandvars`` runs before ``expanduser`` so callers can pass
    shell-style placeholders such as ``$TMPDIR``. Any placeholder that
    still cannot be resolved is rejected with :class:`PathResolutionError`.
    """
    text = os.fspath(raw)
    for match in _PATH_PLACEHOLDER_RE.finditer(text):
        name = match.group("braced") or match.group("bare")
        if not os.environ.get(name):
            raise PathResolutionError(
                f"{context}: unresolved placeholder {match.group(0)!r}"
            )
    return Path(os.path.expandvars(text)).expanduser()


def global_root() -> Path:
    """Return the global agent root directory, creating it on demand.

    Resolution order:

    1. ``ARGUS_SKILL_HOME`` (preferred, explicit override).
    2. ``ARGUS_SKILL_LIFE_DIR``'s parent if that env var points at
       ``<root>/life`` (legacy layout — life used to live at
       ``~/.argus-skill/life``). We accept either the legacy "life dir"
       or the new "global root" as long as the user is consistent.
    3. ``~/.argus-skill``.
    """
    raw = os.environ.get("ARGUS_SKILL_HOME")
    if raw:
        return resolve_runtime_path(raw, context="ARGUS_SKILL_HOME")
    legacy = os.environ.get("ARGUS_SKILL_LIFE_DIR")
    if legacy:
        legacy_path = resolve_runtime_path(legacy, context="ARGUS_SKILL_LIFE_DIR")
        # If the user pointed ARGUS_SKILL_LIFE_DIR at ``…/life`` we
        # treat its parent as the global root, otherwise we treat the
        # value itself as the new root.
        if legacy_path.name == "life" and legacy_path.parent != legacy_path:
            return legacy_path.parent
        return legacy_path
    return Path.home() / ".argus-skill"


# ---------------------------------------------------------------------------
# Top-level files / dirs
# ---------------------------------------------------------------------------

def identity_path() -> Path:
    return global_root() / "identity.md"


def config_path() -> Path:
    """``~/.argus-skill/config.json`` — persisted operator knob overrides
    (backend / model / reasoning-effort switches made via natural language or
    ``/backend``, ``/config``), read by :mod:`argus_skill.core.knob_store`.
    Global (not per-project), same footing as ``identity_path``. Project USD
    budgets live separately in ``projects/<id>/budget.json``."""
    return global_root() / "config.json"


def journal_path() -> Path:
    return global_root() / "journal.jsonl"


def bus_root() -> Path:
    return global_root() / "bus"


def commands_path() -> Path:
    return bus_root() / "commands.jsonl"


def outbox_path() -> Path:
    return bus_root() / "outbox.jsonl"


def status_path() -> Path:
    return bus_root() / "status.json"


def daemon_pid_path() -> Path:
    return bus_root() / "daemon.pid"


def skills_global_root() -> Path:
    return global_root() / "skills"


def skills_archive_root() -> Path:
    return global_root() / "skills" / "_archive"


def tools_root() -> Path:
    """Return the root for optional, externally versioned tool installations."""
    return global_root() / "tools"


def projects_root() -> Path:
    return global_root() / "projects"


# ---------------------------------------------------------------------------
# Per-project subtree
# ---------------------------------------------------------------------------

def project_root(fingerprint: str) -> Path:
    """Return ``~/.argus-skill/projects/<fingerprint>/``.

    Caller is responsible for having computed ``fingerprint`` via
    :func:`argus_skill.core.project.project_fingerprint`. We do not
    re-validate the value here — but we refuse the empty string to
    avoid silently writing into ``projects/``.
    """
    if not fingerprint or "/" in fingerprint or fingerprint.startswith("."):
        raise ValueError(f"invalid project fingerprint: {fingerprint!r}")
    return projects_root() / fingerprint


def project_memory_path(fingerprint: str) -> Path:
    return project_root(fingerprint) / "events.jsonl"


def project_backlog_path(fingerprint: str) -> Path:
    return project_root(fingerprint) / "backlog.jsonl"


def project_skills_root(fingerprint: str) -> Path:
    return project_root(fingerprint) / "skills"


def project_missions_root(fingerprint: str) -> Path:
    return project_root(fingerprint) / "missions"


def mission_root(fingerprint: str, mission_id: str) -> Path:
    if not mission_id or "/" in mission_id or mission_id.startswith("."):
        raise ValueError(f"invalid mission id: {mission_id!r}")
    return project_missions_root(fingerprint) / mission_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dir(path: Path) -> Path:
    """Idempotently ensure a directory exists. Returns the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
