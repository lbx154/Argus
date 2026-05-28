from __future__ import annotations

from typing import Literal


RunnerBackend = Literal["codex", "claude", "copilot"]

BACKEND_CODEX: RunnerBackend = "codex"
BACKEND_CLAUDE: RunnerBackend = "claude"
BACKEND_COPILOT: RunnerBackend = "copilot"
RUNNER_BACKEND_CHOICES: tuple[RunnerBackend, RunnerBackend, RunnerBackend] = (
    BACKEND_CODEX,
    BACKEND_CLAUDE,
    BACKEND_COPILOT,
)
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


def backend_label(backend: RunnerBackend) -> str:
    if backend == BACKEND_CLAUDE:
        return "Claude Code"
    if backend == BACKEND_COPILOT:
        return "GitHub Copilot CLI"
    return "Codex CLI"


def backend_supports_copilot_proxy(backend: RunnerBackend) -> bool:
    return backend == BACKEND_CODEX
