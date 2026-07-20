from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal

RunnerBackend = Literal["codex", "claude", "copilot"]

BACKEND_CODEX: RunnerBackend = "codex"
BACKEND_CLAUDE: RunnerBackend = "claude"
BACKEND_COPILOT: RunnerBackend = "copilot"
DEFAULT_RUNNER_BACKEND: RunnerBackend = BACKEND_CODEX


def normalize_runner_backend(raw: str | None) -> RunnerBackend:
    value = str(raw or "").strip().lower()
    if value == BACKEND_CLAUDE:
        return BACKEND_CLAUDE
    if value == BACKEND_COPILOT:
        return BACKEND_COPILOT
    return BACKEND_CODEX


def default_runner_bin(backend: RunnerBackend) -> str:
    if backend == BACKEND_CLAUDE:
        return "claude"
    if backend == BACKEND_COPILOT:
        return "copilot"
    return "codex"


def resolve_runner_bin(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> str | None:
    """Resolve a CLI independently of service-manager PATH omissions."""
    chosen = normalize_runner_backend(backend)
    requested = str(configured or default_runner_bin(chosen)).strip()
    if not requested:
        return None
    expanded = str(Path(requested).expanduser())
    resolved = shutil.which(expanded)
    if resolved:
        return resolved
    if Path(expanded).parent != Path("."):
        return None
    user_local = Path.home() / ".local" / "bin" / expanded
    if user_local.is_file() and os.access(user_local, os.X_OK):
        return str(user_local)
    return None


def resolve_available_runner(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> tuple[RunnerBackend, str]:
    """Resolve the requested CLI, falling back only when Codex is absent."""
    raw = str(backend or "").strip().lower()
    chosen = normalize_runner_backend(backend)
    resolved = resolve_runner_bin(chosen, configured)
    if resolved:
        return chosen, resolved
    if chosen == BACKEND_CODEX and raw in ("", BACKEND_CODEX):
        copilot = resolve_runner_bin(BACKEND_COPILOT)
        if copilot:
            return BACKEND_COPILOT, copilot
    return chosen, str(configured or default_runner_bin(chosen)).strip()
