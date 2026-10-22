"""Hand a role call's workspace boundary to its agent CLI's pre-tool hook (SE-1).

The boundary itself is :mod:`argus.core.workspace_boundary`. This module only
wires it into each backend's own hook surface, for the duration of one call:

* Claude Code: an inline ``--settings`` ``PreToolUse`` hook (exit 2 refuses);
* Copilot CLI: a Claude-format plugin passed with ``--plugin-dir``, whose
  ``PreToolUse`` hook answers with a ``deny`` decision;
* Codex: ``-c hooks.PreToolUse=...`` with ``--dangerously-bypass-hook-trust``,
  since an inline hook Argus wrote has no persisted trust record;
* Pi: an extension whose ``tool_call`` handler runs the same check.

Each was verified against the real CLI with a scripted local model (see
``tests/agent_cli/test_workspace_boundary_cli.py``). Other backends (Qoder,
Cursor, OpenCode, Grok, dsh) have no hook wired here: their calls run without
the boundary, and the runner says so once per backend.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from ..core.workspace_boundary import hook_command
from .runner_backend import BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_COPILOT, BACKEND_PI

log = logging.getLogger(__name__)

PI_EXTENSION = Path(__file__).with_name("pi_workspace_boundary.mjs")
#: The hook argv the Pi extension runs, JSON-encoded, in the Pi child env.
PI_HOOK_ENV = "ARGUS_PLUGIN_WORKSPACE_BOUNDARY_HOOK"
_PLUGIN_NAME = "argus-workspace-boundary"
_HOOK_TIMEOUT_SECONDS = 30
SUPPORTED_BACKENDS = frozenset({BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_COPILOT, BACKEND_PI})
_UNSUPPORTED_WARNED: set[str] = set()


def _shell(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _hook_group(command: str) -> list[dict]:
    return [{"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": _HOOK_TIMEOUT_SECONDS}]}]


@contextmanager
def workspace_boundary_call(backend: str, options) -> Iterator[object]:
    """Prepare the hook a call's boundary needs; options pass through unchanged
    when the call has no boundary, no tools, or runs in worktree isolation
    (whose process sandbox already hides everything outside the worktree)."""
    boundary = getattr(options, "workspace_boundary", None)
    if boundary is None or options.disable_tools or options.isolate_workdir:
        yield options
        return
    if backend not in SUPPORTED_BACKENDS:
        if backend not in _UNSUPPORTED_WARNED:
            _UNSUPPORTED_WARNED.add(backend)
            log.warning(
                "the %s backend has no pre-tool hook Argus can use, so Engineer and "
                "Reviewer calls on it run without the workspace boundary (SE-1)",
                backend,
            )
        yield options
        return
    with tempfile.TemporaryDirectory(prefix="argus-boundary-") as directory:
        root = Path(directory)
        encoded = boundary.to_json().encode("utf-8")
        path = root / "boundary.json"
        path.write_bytes(encoded)
        path.chmod(0o400)
        digest = hashlib.sha256(encoded).hexdigest()
        fmt = {BACKEND_CLAUDE: "claude", BACKEND_CODEX: "codex", BACKEND_COPILOT: "copilot"}.get(backend, "pi")
        argv = [*hook_command(path, fmt), "--sha256", digest]
        prepared: dict[str, object] = {"_boundary_hook": argv}
        if backend == BACKEND_COPILOT:
            plugin = root / "plugin"
            (plugin / ".claude-plugin").mkdir(parents=True)
            (plugin / "hooks").mkdir()
            (plugin / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": _PLUGIN_NAME, "version": "1.0.0"}), encoding="utf-8",
            )
            (plugin / "hooks" / "hooks.json").write_text(
                json.dumps({"hooks": {"PreToolUse": _hook_group(_shell(argv))}}), encoding="utf-8",
            )
            prepared["plugin_dirs"] = [*(options.plugin_dirs or []), str(plugin)]
        yield replace(options, **prepared)


def boundary_command_args(backend: str, options) -> list[str]:
    """The argv a backend's command needs for the prepared hook, or ``[]``."""
    argv = getattr(options, "_boundary_hook", None)
    if not argv or options.disable_tools:
        return []
    if backend == BACKEND_CLAUDE:
        # Flag settings outrank the user's own, so their ``disableAllHooks``
        # cannot switch this hook off.
        settings = {"disableAllHooks": False, "hooks": {"PreToolUse": _hook_group(_shell(argv))}}
        return ["--settings", json.dumps(settings)]
    if backend == BACKEND_CODEX:
        group = (
            '[{matcher="*",hooks=[{type="command",command='
            + json.dumps(_shell(argv)) + f",timeout={_HOOK_TIMEOUT_SECONDS}" + "}]}]"
        )
        return [
            "-c", "features.hooks=true",
            "-c", f"hooks.PreToolUse={group}",
            "--dangerously-bypass-hook-trust",
        ]
    if backend == BACKEND_PI:
        return ["--extension", str(PI_EXTENSION)]
    return []


def apply_boundary_env(backend: str, options, env: dict[str, str]) -> dict[str, str]:
    """Give the Pi extension its hook argv; other backends need nothing."""
    argv = getattr(options, "_boundary_hook", None)
    env.pop(PI_HOOK_ENV, None)
    if backend == BACKEND_PI and argv and not options.disable_tools:
        env[PI_HOOK_ENV] = json.dumps(argv)
    return env


__all__ = [
    "PI_EXTENSION",
    "PI_HOOK_ENV",
    "SUPPORTED_BACKENDS",
    "apply_boundary_env",
    "boundary_command_args",
    "workspace_boundary_call",
]
