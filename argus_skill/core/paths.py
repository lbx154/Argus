"""Centralised on-disk path layout for the unified argus-skill agent.

Single source of truth for everything under ``~/.argus-skill/``. All
runtime code that needs to read or write any agent state MUST go
through these helpers — never hard-code paths elsewhere.

Directory layout (Phase 0–2 target, see plan.md §2.3)::

    ~/.argus-skill/
    ├─ identity.md
    ├─ journal.jsonl
    ├─ bus/
    │   ├─ commands.jsonl
    │   ├─ commands.jsonl.offset
    │   ├─ outbox.jsonl
    │   ├─ status.json
    │   └─ daemon.pid
    ├─ skills/
    │   └─ *.md
    ├─ reviewer/
    │   └─ lessons.jsonl
    └─ projects/
        └─ <fingerprint>/
            ├─ project.md
            ├─ memory.jsonl
            ├─ backlog.jsonl
            ├─ skills/
            │   └─ *.md
            └─ missions/
                └─ <mission_id>/
                    ├─ mission.json
                    ├─ rounds.jsonl
                    └─ result.json

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
from pathlib import Path

__all__ = [
    "global_root",
    "identity_path",
    "journal_path",
    "bus_root",
    "commands_path",
    "outbox_path",
    "status_path",
    "daemon_pid_path",
    "skills_global_root",
    "skills_archive_root",
    "projects_root",
    "project_root",
    "project_md_path",
    "project_memory_path",
    "project_backlog_path",
    "project_skills_root",
    "project_missions_root",
    "mission_root",
    "ensure_dir",
]


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
        return Path(raw).expanduser()
    legacy = os.environ.get("ARGUS_SKILL_LIFE_DIR")
    if legacy:
        legacy_path = Path(legacy).expanduser()
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


def project_md_path(fingerprint: str) -> Path:
    return project_root(fingerprint) / "project.md"


def project_memory_path(fingerprint: str) -> Path:
    return project_root(fingerprint) / "memory.jsonl"


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
