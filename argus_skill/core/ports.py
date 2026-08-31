"""Core protocols (ports) the loop integrates against.

Provenance: the ``EventSink`` shape is adapted from ArgusBot's
``core/ports.py``. ``RunnerBackend`` is new — it sits at the
seam where ArgusBot's hard-coded ``AgentCliRunner`` used to be, and where
skill-agent's ``codex_exec(...)`` callable used to be. By making it a
``Protocol`` we can plug in:

  * ``AgentCliBackend`` — drives the codex / claude / copilot / cursor / opencode / pi /
    grok/dsh CLIs.
  * ``MemoryBackend`` — deterministic stub for tests / CI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .models import RunnerOptions, RunnerResult

# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------

class RunnerBackend(Protocol):
    """One LLM-CLI invocation. Both engineer and reviewer call this."""

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        ...


class ToolActivityObservable(Protocol):
    """Optional runner capability for authoritative tool-use telemetry."""

    @property
    def tool_activity_observation_supported(self) -> bool: ...


# ---------------------------------------------------------------------------
# Skill source protocol
# ---------------------------------------------------------------------------

class SkillSource(Protocol):
    """Path-only Skill-library surface exposed to Agents."""

    def library_roots(self) -> list[Path]:
        ...


# ---------------------------------------------------------------------------
# Event sink (daemon / interactive modes consume structured events).
# ---------------------------------------------------------------------------

class EventSink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> bool | None: ...

    def handle_stream_line(self, stream: str, line: str) -> None: ...

    def close(self) -> None: ...
