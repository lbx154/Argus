"""Runtime checkout identity for cross-process compatibility diagnostics."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import __version__
from ..release import release_identity

_PROCESS_STARTED_AT = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def source_root() -> Path:
    """Return the checkout or installed package root that loaded this process."""
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def source_revision() -> str | None:
    configured = os.environ.get("ARGUS_SKILL_BUILD_REVISION", "").strip()
    if configured:
        return configured
    root = source_root()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip() if result.returncode == 0 else ""
    return revision or None


@lru_cache(maxsize=1)
def source_worktree_state() -> dict[str, Any]:
    root = source_root()
    try:
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=root, check=False, capture_output=True, text=True, timeout=1.0,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, check=False, capture_output=True, text=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git_available": False, "branch": None, "detached": None, "dirty": None}
    return {
        "git_available": status.returncode == 0,
        "branch": branch or None,
        "detached": not bool(branch),
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def configured_source_root() -> str:
    """Return the deployment source root the operator expects, or ``""``.

    Explicit process env wins over the persisted cockpit knob — the standard
    ``env > core.knob_store > unset`` precedence every operator knob uses —
    so a supervised deployment can pin the root once in ``config.json`` and
    every later daemon/WebAPI boot still honors it.
    """
    from .knob_store import persisted_knob

    return persisted_knob("ARGUS_SKILL_SOURCE_ROOT").strip()


def runtime_identity() -> dict[str, Any]:
    from .knob_store import KnobStoreCorruptError

    try:
        configured_root = configured_source_root()
    except KnobStoreCorruptError:
        # This payload is pure diagnostics, and its consumers (webapi
        # /api/meta, the daemon status writer, handoff-candidate reads) catch
        # only OSError — a corrupt config.json must degrade to "unconfigured"
        # here, not take the status surfaces down with it. Enforcement stays
        # strict in source_root_preflight_error(), where the corruption is
        # itself a fail-closed startup refusal.
        configured_root = ""
    loaded_root = source_root()
    return {
        "package_version": __version__,
        "source_root": str(loaded_root),
        "configured_source_root": configured_root or None,
        "source_root_matches_config": (
            None
            if not configured_root
            else Path(configured_root).expanduser().resolve() == loaded_root
        ),
        "revision": source_revision(),
        "worktree": source_worktree_state(),
        "pid": os.getpid(),
        "python_version": platform.python_version(),
        "executable": sys.executable,
        "started_at": _PROCESS_STARTED_AT,
        **release_identity(loaded_root),
    }


def release_match_preflight_error() -> str:
    """Return a strict-startup error for a half-upgraded source release.

    Editable development remains permissive by default. Supervised production
    services opt in with ``ARGUS_SKILL_REQUIRE_RELEASE_MATCH=1`` so backend and
    frontend artifacts cannot start from different source identities.
    """
    required = str(
        os.environ.get("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", "")
    ).strip().lower()
    if required not in {"1", "true", "yes", "on"}:
        return ""
    identity = runtime_identity()
    if identity.get("release_matches_source") is False:
        return (
            "loaded source does not match the prebuilt release artifacts; "
            "pull a complete published revision and reinstall with `pip install -e .`"
        )
    return ""


def source_root_preflight_error() -> str:
    """Return a strict-startup error for a process loaded from the wrong checkout.

    Unconfigured operation stays permissive. Once ``ARGUS_SKILL_SOURCE_ROOT``
    is set (env or persisted knob) it is a startup contract: a rewritten
    launcher that imports Argus from some other worktree — the user-site
    editable-install hijack — is refused here instead of silently serving
    stale code. Both sides are compared fully resolved so a deploy root
    reached through a symlink still counts as the same checkout.
    """
    configured = configured_source_root()
    if not configured:
        return ""
    loaded = source_root()
    if Path(configured).expanduser().resolve() == loaded:
        return ""
    return (
        f"process loaded source root {loaded} but ARGUS_SKILL_SOURCE_ROOT "
        f"expects {configured}; the launcher resolves to the wrong checkout — "
        "reinstall with `pip install -e .` from the configured root, or fix "
        "the configured root"
    )


__all__ = [
    "configured_source_root",
    "release_match_preflight_error",
    "runtime_identity",
    "source_revision",
    "source_root",
    "source_root_preflight_error",
    "source_worktree_state",
]
