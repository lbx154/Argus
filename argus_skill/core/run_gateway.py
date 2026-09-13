"""Single application-level gateway for every ``RunnerBackend`` invocation."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, Callable, Iterator

from .models import RunnerOptions, RunnerResult
from .ports import RunnerBackend

_RESUME_UNSET = object()
_INTERRUPT: ContextVar[Callable[[], str | None] | None] = ContextVar("argus_run_interrupt", default=None)


@contextmanager
def run_interrupt_scope(
    provider: Callable[[], str | None], *, retain_first_reason: bool = False,
) -> Iterator[None]:
    """Attach request cancellation without mutating cached runners.

    A caller that polls during preparation can retain its first reason until
    this scope exits, so a one-shot abort also reaches subsequent provider calls.
    """
    previous = _INTERRUPT.get()
    first_reason: str | None = None
    reason_lock = Lock()

    def interrupt_reason() -> str | None:
        nonlocal first_reason
        if retain_first_reason:
            with reason_lock:
                if first_reason:
                    return first_reason
        # Poll outside the lock: a slow inherited state read must not prevent
        # another thread in a copied context from observing the current Stop.
        reason = provider() or (previous() if previous else None)
        if retain_first_reason:
            with reason_lock:
                first_reason = first_reason or reason
                return first_reason
        return reason

    token = _INTERRUPT.set(interrupt_reason)
    try:
        yield
    finally:
        _INTERRUPT.reset(token)


def current_run_interrupt_reason() -> str | None:
    """Let synchronous call preparation observe the active cancellation scope."""
    provider = _INTERRUPT.get()
    return provider() if provider is not None else None


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
        options = request.options
        interrupt = _INTERRUPT.get()
        if interrupt is not None and (options is None or isinstance(options, RunnerOptions)):
            options = options or RunnerOptions()
            original = options.external_interrupt_reason_provider
            options = replace(options, external_interrupt_reason_provider=(
                lambda: interrupt() or (original() if original else None)
            ))
        kwargs = {
            "prompt": str(request.prompt),
            "options": options,
            "run_label": str(request.run_label),
        }
        if request.resume_thread_id is not _RESUME_UNSET:
            kwargs["resume_thread_id"] = request.resume_thread_id
        reason = interrupt() if interrupt is not None else None
        result = (RunnerResult(exit_code=130, fatal_error=f"External interrupt: {reason}")
                  if reason else self.backend.run_exec(**kwargs))
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


__all__ = ["RunExecGateway", "RunExecRequest", "run_exec", "run_interrupt_scope", "current_run_interrupt_reason"]
