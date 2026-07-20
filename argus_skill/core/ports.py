"""Core protocols (ports) the loop integrates against.

Provenance: the ``EventSink`` shape is adapted from ArgusBot's
``core/ports.py``. ``RunnerBackend`` is new — it sits at the
seam where ArgusBot's hard-coded ``AgentCliRunner`` used to be, and where
skill-agent's ``codex_exec(...)`` callable used to be. By making it a
``Protocol`` we can plug in:

  * ``AgentCliBackend`` — drives the codex / claude / copilot / opencode CLIs.
  * ``MemoryBackend`` — deterministic stub for tests / CI.
"""
from __future__ import annotations

from collections.abc import Callable
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


# ---------------------------------------------------------------------------
# Skill source protocol
# ---------------------------------------------------------------------------

class SkillSource(Protocol):
    """The minimum surface SkillLoop needs from a skill backend.

    The default implementation (``SkillStore``) is on-disk markdown.
    Alternative implementations could front a remote API or a vector
    index without changing the loop.
    """

    def find_relevant(
        self,
        task_description: str,
        on_event: Callable[[dict], None] | None = None,
        *,
        role: str | None = None,
        exclude_files: set[str] | None = None,
        force_empty_match: bool = False,
    ) -> tuple[list[Any] | None, int]:
        ...

    def render_skill(self, skill: Any, *, full: bool = False) -> str:
        """Render a skill into the prompt-injectable string form."""
        ...

    def list_summaries(self) -> list[dict]:
        ...

    def save_distilled(
        self,
        *,
        task_description: str,
        raw_distill_output: str,
    ) -> Any:
        ...


# ---------------------------------------------------------------------------
# Event sink (daemon / interactive modes consume structured events).
# ---------------------------------------------------------------------------

class EventSink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> bool | None: ...

    def handle_stream_line(self, stream: str, line: str) -> None: ...

    def close(self) -> None: ...
