"""Pre-spawn admission for the agent-CLI backend.

:func:`admit` performs every pre-spawn check in strict order:

1. Runner CLI option translation.
2. External-interrupt check.
3. Copilot circuit permit (a concurrency slot plus the policy/rate cooldown).

On any admission failure the function returns ``(None, RunnerResult)`` where
the result is already fully finalised (secrets redacted, usage record
persisted if applicable, metric emitted).  The caller must forward that
result immediately; no subprocess must be started.

On success it returns ``(cli_options, None)`` — the caller may proceed to the
spawn phase with the translated ``cli_options``.

Spending is never a reason to refuse a call here: every call is recorded in
the usage ledger for display, and a hosted trial is limited at its gateway.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ...core.models import RunnerResult
from ...core.stop_kinds import normalize_stop_kind, stop_kind_from_external_interrupt
from ._exec_finalize import finalize_result
from ._options import _interrupt_reason

if TYPE_CHECKING:
    from ._exec_context import _ExecContext

log = logging.getLogger(__name__)


def admit(ctx: "_ExecContext") -> tuple[Any, RunnerResult | None]:
    """Run all pre-spawn admission checks; mutate *ctx* with the acquired permit.

    Returns ``(cli_options, None)`` when the call is admitted and ready to
    spawn.  Returns ``(None, RunnerResult)`` when the call is denied; the
    returned result is already finalised (secrets redacted, usage recorded).
    """
    backend = ctx.backend

    # ------------------------------------------------------------------ #
    # 1. CLI option translation                                            #
    # ------------------------------------------------------------------ #
    try:
        cli_options = backend._translate_options(ctx.options)
    except Exception as exc:  # noqa: BLE001 - refuse cleanly on setup failure
        reason = f"runner option translation failed: {type(exc).__name__}: {exc}"
        return None, finalize_result(
            ctx,
            RunnerResult(
                exit_code=-1,
                thread_id=ctx.resume_thread_id,
                fatal_error=f"refused before start: {reason}",
                stop_kind="permanent_error",
            ),
            status="denied",
            error=reason,
        )

    # ------------------------------------------------------------------ #
    # 2. External interrupt                                                #
    # ------------------------------------------------------------------ #
    interrupted = (
        _interrupt_reason(
            getattr(cli_options, "external_interrupt_reason_provider", None)
        )
        if backend._is_copilot or backend._is_codex
        else None
    )
    if interrupted:
        reason = f"External interrupt: {interrupted}"
        return None, finalize_result(
            ctx,
            RunnerResult(
                exit_code=-1,
                thread_id=ctx.resume_thread_id,
                fatal_error=reason,
                stop_kind=stop_kind_from_external_interrupt(reason),
            ),
            status="denied",
            error=reason,
        )

    # ------------------------------------------------------------------ #
    # 3. Copilot circuit permit                                            #
    # ------------------------------------------------------------------ #
    copilot_permit = None
    if backend._is_copilot:
        from ...provider_integrations.copilot_guard import (
            acquire_copilot_permit,
            release_denied_permit,
        )

        copilot_permit = acquire_copilot_permit(ctx.run_label)
        if not copilot_permit.allowed:
            reason = copilot_permit.reason
            release_denied_permit(copilot_permit)
            backend._log_agent_io(ctx.log_path, {
                "type": EventType.PROVIDER_REQUEST_DENIED,
                "provider": "copilot",
                "call_id": ctx.call_id,
                "run_label": ctx.run_label,
                "reason": reason,
                "ts": time.time(),
            })
            log.warning(
                "Copilot call blocked before start (%s): %s",
                ctx.run_label,
                reason,
            )
            return None, finalize_result(
                ctx,
                RunnerResult(
                    exit_code=-1,
                    thread_id=ctx.resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind=normalize_stop_kind(copilot_permit.stop_kind),
                ),
                status="denied",
                error=reason,
            )

    # ------------------------------------------------------------------ #
    # Admission granted — store the permit in context, log started event   #
    # ------------------------------------------------------------------ #
    ctx.copilot_permit = copilot_permit
    ctx.event_permit = (
        copilot_permit
        if copilot_permit is not None and bool(getattr(copilot_permit, "guarded", True))
        else None
    )
    if ctx.event_permit is not None:
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.PROVIDER_REQUEST_STARTED,
            "provider": backend._backend_name,
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "ts": time.time(),
        })

    return cli_options, None
