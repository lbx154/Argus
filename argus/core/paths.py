"""Canonical on-disk paths under ``ARGUS_SKILL_HOME``.

The runtime root contains host-wide configuration and shared capabilities.
Session state lives under the historically named ``projects/`` directory; the
directory name remains for on-disk compatibility, but APIs call it session state
so it cannot be confused with the roles' separate execution ``workdir``.

This module only resolves paths. Callers that write data create directories at
their write boundary.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = [
    "global_root",
    "identity_path",
    "config_path",
    "shared_skills_root",
    "shared_skills_archive_root",
    "reviewed_facts_digest_path",
    "tools_root",
    "capabilities_root",
    "special_prompts_root",
    "logs_root",
    "run_root",
    "session_states_root",
    "session_state_root",
    "session_trash_root",
    "PathResolutionError",
    "resolve_runtime_path",
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
    """Return the runtime root without creating it."""
    raw = os.environ.get("ARGUS_SKILL_HOME")
    if raw:
        return resolve_runtime_path(raw, context="ARGUS_SKILL_HOME")
    return Path.home() / ".argus-skill"


def _root(root: str | Path | None) -> Path:
    return resolve_runtime_path(root, context="runtime root") if root is not None else global_root()


def identity_path(root: str | Path | None = None) -> Path:
    return _root(root) / "identity.md"


def config_path(root: str | Path | None = None) -> Path:
    return _root(root) / "config.json"


def shared_skills_root(root: str | Path | None = None) -> Path:
    return _root(root) / "skills"


def shared_skills_archive_root(root: str | Path | None = None) -> Path:
    return shared_skills_root(root) / "_archive"


def reviewed_facts_digest_path(root: str | Path | None = None) -> Path:
    return _root(root) / "reviewed-facts.md"


def tools_root(root: str | Path | None = None) -> Path:
    return _root(root) / "tools"


def capabilities_root(root: str | Path | None = None) -> Path:
    return _root(root) / "capabilities"


def special_prompts_root(root: str | Path | None = None) -> Path:
    return _root(root) / "special_prompts"


def logs_root(root: str | Path | None = None) -> Path:
    return _root(root) / "logs"


def run_root(root: str | Path | None = None) -> Path:
    return _root(root) / "run"


def session_states_root(root: str | Path | None = None) -> Path:
    """Return the session-state collection (kept on disk as ``projects/``)."""
    return _root(root) / "projects"


def _safe_component(value: str, *, label: str) -> str:
    if (
        not value
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def session_state_root(
    session_id: str,
    *,
    root: str | Path | None = None,
) -> Path:
    """Return one session's internal state root, never its execution workdir."""
    return session_states_root(root) / _safe_component(session_id, label="session id")


def session_trash_root(root: str | Path | None = None) -> Path:
    return _root(root) / "projects_trash"
