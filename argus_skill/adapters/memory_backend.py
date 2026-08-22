"""In-memory deterministic RunnerBackend for tests, smoke runs, and CI.

The ``MemoryBackend`` looks up canned responses by ``run_label``. Each
label can have a queue of responses; consecutive calls pop from the
queue. When the queue is exhausted, a default response is used.

Provenance: new code, written for argus-skill. The interface mirrors
``argus_skill.core.ports.RunnerBackend`` exactly so tests are realistic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..core.models import RunnerOptions, RunnerResult


@dataclass
class CannedResponse:
    """A scripted response to a single ``run_exec`` call.

    ``message_factory`` is preferred when the response should depend on
    the prompt (e.g. distiller emitting a skill that mentions the task);
    otherwise a literal ``message`` is enough.
    """
    message: str = ""
    message_factory: Callable[[str, RunnerOptions], str] | None = None
    exit_code: int = 0
    fatal_error: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thread_id: str | None = None
    orphan_process_group_id: int = 0
    orphan_process_group_cleanup_succeeded: bool = False

    def render(self, prompt: str, options: RunnerOptions) -> RunnerResult:
        if self.message_factory is not None:
            text = self.message_factory(prompt, options)
        else:
            text = self.message
        return RunnerResult(
            exit_code=self.exit_code,
            agent_messages=[text] if text else [],
            thread_id=self.thread_id,
            fatal_error=self.fatal_error,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            orphan_process_group_id=self.orphan_process_group_id,
            orphan_process_group_cleanup_succeeded=(
                self.orphan_process_group_cleanup_succeeded
            ),
        )


@dataclass
class MemoryBackend:
    """Deterministic test backend.

    Usage::

        backend = MemoryBackend()
        backend.queue("matcher", CannedResponse(message='{"matched": []}'))
        backend.queue("distiller", CannedResponse(message_factory=lambda p, o: SKILL_MD))
        backend.queue("engineer-r1", CannedResponse(message="round 1 work"))
        backend.queue("reviewer", CannedResponse(message='{"status":"continue", ...}'))
        backend.queue("engineer-r2", CannedResponse(message="round 2 work"))
        backend.queue("reviewer", CannedResponse(message='{"status":"done", ...}'))

    Calls without a queued response return ``default``.
    """
    default: CannedResponse = field(default_factory=lambda: CannedResponse(
        message="(no canned response)",
    ))
    _queues: dict[str, list[CannedResponse]] = field(default_factory=dict)
    history: list[tuple[str, str, RunnerOptions]] = field(default_factory=list)
    resume_history: list[tuple[str, str | None]] = field(default_factory=list)

    @property
    def tool_activity_observation_supported(self) -> bool:
        return False

    def queue(self, run_label: str, *responses: CannedResponse) -> None:
        bucket = self._queues.setdefault(run_label, [])
        bucket.extend(responses)

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.history.append((run_label, prompt, options))
        self.resume_history.append((run_label, resume_thread_id))
        bucket = self._queues.get(run_label, [])
        response = bucket.pop(0) if bucket else self.default
        return response.render(prompt, options)
