"""Single application-level gateway for every ``RunnerBackend`` invocation."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from .models import RunnerResult
from .ports import RunnerBackend

_RESUME_UNSET = object()


@dataclass(frozen=True)
class RunExecRequest:
    prompt: str
    run_label: str
    options: Any = None
    resume_thread_id: str | None | object = _RESUME_UNSET


class RunExecGateway:
    """Normalize application calls before delegating to one backend adapter.

    Provider subprocess handling, usage extraction, and provider-specific quota
    logic remain adapter responsibilities. Everything above adapters calls this
    gateway, giving tracing/metrics/policy one stable interception point.
    """

    def __init__(self, backend: RunnerBackend) -> None:
        self.backend = backend

    def execute(self, request: RunExecRequest) -> Any:
        started_at = time.time()
        kwargs = {
            "prompt": str(request.prompt),
            "options": request.options,
            "run_label": str(request.run_label),
        }
        if request.resume_thread_id is not _RESUME_UNSET:
            kwargs["resume_thread_id"] = request.resume_thread_id
        result = self.backend.run_exec(**kwargs)
        completed_at = time.time()
        if not isinstance(result, RunnerResult):
            return result
        if not result.call_id:
            result.call_id = f"gateway-{uuid.uuid4().hex}"
        if result.thread_id is None and isinstance(request.resume_thread_id, str):
            result.thread_id = request.resume_thread_id
        if result.started_at <= 0:
            result.started_at = started_at
        if result.completed_at <= 0:
            result.completed_at = completed_at
        if result.duration_ms <= 0:
            result.duration_ms = max(
                0,
                int(round((result.completed_at - result.started_at) * 1000)),
            )
        from .operator_context import operator_context_revision_from_text

        result.operator_context_revision = operator_context_revision_from_text(
            request.prompt
        )
        return result


def run_exec(
    backend: RunnerBackend,
    *,
    prompt: str,
    run_label: str,
    options: Any = None,
    resume_thread_id: str | None | object = _RESUME_UNSET,
) -> Any:
    """Convenience entry point used by application code."""
    return RunExecGateway(backend).execute(
        RunExecRequest(
            prompt=prompt,
            options=options,
            run_label=run_label,
            resume_thread_id=resume_thread_id,
        )
    )


__all__ = ["RunExecGateway", "RunExecRequest", "run_exec"]
