"""Pre-spawn admission for the agent-CLI backend.

:func:`admit` performs every pre-spawn check in strict order:

1. Runner CLI option translation.
2. External-interrupt check.

Every backend is admitted the same way; the host-wide concurrency slot is
taken by the caller before this runs.

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

from typing import TYPE_CHECKING, Any

from ...core.models import RunnerResult
from ...core.stop_kinds import stop_kind_from_external_interrupt
from ._exec_finalize import finalize_result
from ._options import _interrupt_reason

if TYPE_CHECKING:
    from ._exec_context import _ExecContext



def admit(ctx: "_ExecContext") -> tuple[Any, RunnerResult | None]:
    """Run the pre-spawn admission checks.

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
    interrupted = _interrupt_reason(
        getattr(cli_options, "external_interrupt_reason_provider", None)
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

    return cli_options, None
