"""Durable provider-session identity for a NEW Copilot CLI session.

A cold Copilot call used to learn its session id only from the terminal
``result`` event. When the runner's watchdog killed the CLI first (turn cap,
idle timeout, external interrupt) that id was lost, and the priced usage rows
the CLI had already written under it could never be reconciled (issue #129).

Copilot CLI accepts ``--session-id <uuid>`` to *set* the identity of a new
session, so Argus allocates the UUID itself, binds it to the call before the
process exists, and hands the same value to the CLI. The flag is
feature-detected from the actual executable's help text; a CLI without it is
reported rather than silently trusted to honour a binding it cannot honour.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import uuid

COPILOT_SESSION_ID_FLAG = "--session-id"

_PROBE_TIMEOUT_SECONDS = 20.0
_support_lock = threading.Lock()
_session_id_support: dict[str, bool] = {}


def new_copilot_session_id() -> str:
    """A fresh provider-session UUID in the form Copilot CLI accepts."""
    return str(uuid.uuid4())


def _copilot_help_text(executable: str) -> str:
    """Read the executable's own ``--help`` output; empty when unavailable."""
    try:
        completed = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{completed.stdout or ''}\n{completed.stderr or ''}"


def copilot_cli_supports_session_id(executable: str | None) -> bool:
    """Feature-detect ``--session-id`` on the executable Argus will spawn.

    The answer is cached per executable for the life of the process: the CLI
    binary does not change between calls, and a Node start-up per call would
    be a real cost. An unresolvable or failing executable reports ``False``;
    the spawn itself then reports the real failure.
    """
    name = str(executable or "").strip()
    if not name:
        return False
    with _support_lock:
        cached = _session_id_support.get(name)
    if cached is not None:
        return cached
    resolved = name if ("/" in name or "\\" in name) else shutil.which(name)
    supported = bool(resolved) and COPILOT_SESSION_ID_FLAG in _copilot_help_text(
        str(resolved)
    )
    with _support_lock:
        _session_id_support[name] = supported
    return supported


def reset_copilot_session_id_support_cache() -> None:
    """Forget probe results (tests, or after the CLI is upgraded in-process)."""
    with _support_lock:
        _session_id_support.clear()


__all__ = [
    "COPILOT_SESSION_ID_FLAG",
    "copilot_cli_supports_session_id",
    "new_copilot_session_id",
    "reset_copilot_session_id_support_cache",
]
