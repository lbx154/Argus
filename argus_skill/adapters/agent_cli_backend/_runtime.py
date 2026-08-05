"""Runtime dependency loading for the agent CLI backend.

The only supported runner implementation is the bundled
``argus_skill.agent_cli`` package (see ``argus_skill/agent_cli/_VENDORED.md``
for its provenance). This module resolves that runtime lazily so importing
``argus_skill.adapters.agent_cli_backend`` never eagerly pulls in the
subprocess driver, and raises a friendly error when the bundled module is
somehow missing (e.g. a broken/partial install).
"""
from __future__ import annotations

from typing import Any


def load_agent_cli_runtime() -> dict[str, Any]:
    """Resolve the bundled ``argus_skill.agent_cli`` runner runtime.

    Returns a dict of the symbols :class:`AgentCliBackend` needs: the
    ``AgentCliRunner`` class, its ``RunnerOptions`` dataclass (returned here
    as ``"CliRunnerOptions"``), the backend name constants, and the
    ``default_runner_bin`` / ``normalize_runner_backend`` helpers.
    """
    try:
        from argus_skill.agent_cli.agent_cli_runner import (
            AgentCliRunner,
        )
        from argus_skill.agent_cli.agent_cli_runner import (
            RunnerOptions as CliRunnerOptions,
        )
        from argus_skill.agent_cli.runner_backend import (
            BACKEND_CLAUDE,
            BACKEND_CODEX,
            BACKEND_COPILOT,
            BACKEND_OPENCODE,
            BACKEND_PI,
            DEFAULT_RUNNER_BACKEND,
            default_runner_bin,
            normalize_runner_backend,
        )
    except ImportError as exc:  # pragma: no cover - environmental
        raise ImportError(
            "AgentCliBackend requires the bundled argus_skill.agent_cli "
            "module. Reinstall argus-skill to restore it."
        ) from exc
    return {
        "AgentCliRunner": AgentCliRunner,
        "CliRunnerOptions": CliRunnerOptions,
        "BACKEND_CLAUDE": BACKEND_CLAUDE,
        "BACKEND_CODEX": BACKEND_CODEX,
        "BACKEND_COPILOT": BACKEND_COPILOT,
        "BACKEND_OPENCODE": BACKEND_OPENCODE,
        "BACKEND_PI": BACKEND_PI,
        "DEFAULT_RUNNER_BACKEND": DEFAULT_RUNNER_BACKEND,
        "default_runner_bin": default_runner_bin,
        "normalize_runner_backend": normalize_runner_backend,
    }
