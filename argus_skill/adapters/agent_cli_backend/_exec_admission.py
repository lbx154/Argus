"""Cost admission and provider quota acquisition for the agent-CLI backend.

:func:`admit` performs every pre-spawn check in strict order:

1. USD cost reservation (fail-closed — any exception denies the call before
   any provider spend is incurred).
2. Runner CLI option translation.
3. External-interrupt check.
4. Provider quota permit acquisition (Copilot **or** Codex; mutually
   exclusive).

On any admission failure the function returns ``(None, RunnerResult)`` where
the result is already fully finalised (secrets redacted, usage record
persisted if applicable, reservation released, metric emitted).  The caller
must forward that result immediately; no subprocess must be started.

On success it returns ``(cli_options, None)`` — the caller may proceed to the
spawn phase with the translated ``cli_options``.

**Fail-closed contract**: any unhandled exception from the cost-control layer
is caught here and treated as a denial so that the call is never spawned when
accounting is uncertain.  Do not remove or widen the ``except Exception``
guard around the reservation block.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ...core.models import RunnerResult
from ...core.stop_kinds import normalize_stop_kind, stop_kind_from_external_interrupt
from ._exec_finalize import finalize_result
from ._options import _interrupt_reason, resolve_pricing_model
from ._result import _reservation_denial_stop_kind

if TYPE_CHECKING:
    from ._exec_context import _ExecContext

log = logging.getLogger(__name__)


def admit(ctx: "_ExecContext") -> tuple[Any, RunnerResult | None]:
    """Run all pre-spawn admission checks; mutate *ctx* with acquired permits.

    Returns ``(cli_options, None)`` when the call is admitted and ready to
    spawn.  Returns ``(None, RunnerResult)`` when the call is denied; the
    returned result is already finalised (secrets redacted, usage recorded,
    reservation settled/released).
    """
    backend = ctx.backend

    # ------------------------------------------------------------------ #
    # 1. Cost admission — fail-closed before any provider spend            #
    # ------------------------------------------------------------------ #
    reservation_model = resolve_pricing_model(
        None, ctx.options.model, None,
    )[0]
    try:
        from ...core.cost_control import (
            cost_control_enabled,
            reserve_call_budget,
        )

        if cost_control_enabled():
            cost_reservation, reserve_reason = reserve_call_budget(
                call_id=ctx.call_id,
                project_root=ctx.usage_project_root,
                mission_id=ctx.usage_mission_id,
                provider=backend._backend_name,
                model=reservation_model,
                run_label=ctx.run_label,
                global_root=ctx.usage_global_root,
            )
            if cost_reservation is None:
                backend._log_agent_io(ctx.log_path, {
                    "type": EventType.BUDGET_RESERVATION_DENIED,
                    "call_id": ctx.call_id,
                    "provider": backend._backend_name,
                    "model": reservation_model,
                    "run_label": ctx.run_label,
                    "reason": reserve_reason,
                })
                return None, finalize_result(
                    ctx,
                    RunnerResult(
                        exit_code=-1,
                        thread_id=ctx.resume_thread_id,
                        fatal_error=f"refused before start: {reserve_reason}",
                        stop_kind=_reservation_denial_stop_kind(reserve_reason),
                    ),
                    status="denied",
                    error=reserve_reason,
                )
            ctx.cost_reservation = cost_reservation
            backend._log_agent_io(ctx.log_path, {
                "type": EventType.BUDGET_RESERVATION_CREATED,
                "reservation_id": cost_reservation.reservation_id,
                "call_id": ctx.call_id,
                "provider": backend._backend_name,
                "model": reservation_model,
                "run_label": ctx.run_label,
                "amount_usd": cost_reservation.amount_usd,
            })
    except Exception as exc:  # noqa: BLE001 — fail closed before provider spend
        reason = f"cost control unavailable: {type(exc).__name__}: {exc}"
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.BUDGET_RESERVATION_DENIED,
            "call_id": ctx.call_id,
            "provider": backend._backend_name,
            "model": reservation_model,
            "run_label": ctx.run_label,
            "reason": reason,
        })
        return None, finalize_result(
            ctx,
            RunnerResult(
                exit_code=-1,
                thread_id=ctx.resume_thread_id,
                fatal_error=f"refused before start: {reason}",
                stop_kind="backend_unavailable",
            ),
            status="denied",
            error=reason,
        )

    # ------------------------------------------------------------------ #
    # 2. CLI option translation                                            #
    # ------------------------------------------------------------------ #
    try:
        cli_options = backend._translate_options(ctx.options)
    except Exception as exc:  # noqa: BLE001 - release reservation on setup failure
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
    # 3. External interrupt + provider quota permits                       #
    # ------------------------------------------------------------------ #
    copilot_permit = None
    codex_permit = None
    codex_quota_active = False
    if backend._is_codex:
        from ...core.provider_quota import codex_quota_enabled

        codex_quota_active = codex_quota_enabled()
    interrupted = (
        _interrupt_reason(
            getattr(cli_options, "external_interrupt_reason_provider", None)
        )
        if backend._is_copilot or codex_quota_active
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
    if backend._is_copilot and not interrupted:
        from ...core.copilot_guard import (
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
    elif backend._is_codex and not interrupted:
        from ...core.provider_quota import acquire_codex_permit

        codex_permit = acquire_codex_permit(ctx.run_label)
        if not codex_permit.allowed:
            reason = codex_permit.reason
            backend._log_agent_io(ctx.log_path, {
                "type": EventType.PROVIDER_REQUEST_DENIED,
                "provider": "codex",
                "call_id": ctx.call_id,
                "run_label": ctx.run_label,
                "reason": reason,
                "daily_calls": codex_permit.daily_calls,
                "daily_cap": codex_permit.daily_cap,
                "ts": time.time(),
            })
            log.warning(
                "Codex call blocked before start (%s): %s",
                ctx.run_label,
                reason,
            )
            return None, finalize_result(
                ctx,
                RunnerResult(
                    exit_code=-1,
                    thread_id=ctx.resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind=normalize_stop_kind(codex_permit.stop_kind),
                ),
                status="denied",
                error=reason,
            )

    # ------------------------------------------------------------------ #
    # Admission granted — store permits in context, log started event      #
    # ------------------------------------------------------------------ #
    ctx.codex_quota_active = codex_quota_active
    ctx.copilot_permit = copilot_permit
    ctx.codex_permit = codex_permit
    ctx.quota_permit = copilot_permit or codex_permit
    ctx.event_permit = (
        ctx.quota_permit
        if ctx.quota_permit is not None
        and bool(getattr(ctx.quota_permit, "guarded", True))
        else None
    )
    if ctx.event_permit is not None:
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.PROVIDER_REQUEST_STARTED,
            "provider": backend._backend_name,
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "daily_calls": int(getattr(ctx.event_permit, "daily_calls", 0) or 0),
            "daily_cap": int(getattr(ctx.event_permit, "daily_cap", 0) or 0),
            "premium_requests_today": float(
                getattr(ctx.event_permit, "premium_requests_today", 0.0) or 0.0
            ),
            "premium_cap": float(
                getattr(ctx.event_permit, "premium_cap", 0.0) or 0.0
            ),
            "ts": time.time(),
        })

    return cli_options, None
