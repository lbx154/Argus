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

