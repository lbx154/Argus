"""Result finalization and quota settlement for the agent-CLI execution path.

:func:`finalize_result` is the terminal operation for *every* exit path in
``execute()``.  It redacts secrets from the result, stamps call metadata,
persists the usage record to the durable ledger, settles or releases the
cost reservation, emits a ``provider.call`` metric, and closes the I/O
context.

:func:`finish_quota` finalises the provider-quota permit lifecycle and emits
the ``provider.request.completed`` event.  It is called on every path that
got past the subprocess spawn (success, translation failure, subprocess
exception) but *not* on pre-spawn admission-denial paths.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ...core.codex_usage import TokenUsage
from ...core.event_catalog import EventType
from ...core.metrics import metrics_root_for_project, record_metric
from ...core.models import RunnerResult
from ...core.secret_guard import redact_secrets_text
from ...core.stop_kinds import normalize_stop_kind
from ._options import resolve_pricing_model

if TYPE_CHECKING:
    from ._exec_context import _ExecContext

log = logging.getLogger(__name__)


def finalize_result(
    ctx: "_ExecContext",
    result: RunnerResult,
    *,
    status: str,
    token_usage: TokenUsage | None = None,
    premium_requests: float | None = None,
    error: str = "",
) -> RunnerResult:
    backend = ctx.backend
    persisted_error = redact_secrets_text(
        error or str(result.fatal_error or ""),
        known_values=backend._known_secret_values,
    )
    result.fatal_error = redact_secrets_text(
        str(result.fatal_error or ""),
        known_values=backend._known_secret_values,
    ) or None
    result.agent_messages = [
        redact_secrets_text(
            message,
            known_values=backend._known_secret_values,
        )
        for message in result.agent_messages
    ]
    result.stdout_lines = [
        redact_secrets_text(
            line,
            known_values=backend._known_secret_values,
        )
        for line in result.stdout_lines
    ]
    result.stderr_lines = [
        redact_secrets_text(
            line,
            known_values=backend._known_secret_values,
        )
        for line in result.stderr_lines
    ]
    completed_at = time.time()
    usage_record = None
    result.call_id = ctx.call_id
    result.call_id_log_correlated = True
    result.stop_kind = normalize_stop_kind(result.stop_kind)
    result.thread_id = result.thread_id or ctx.resume_thread_id
    result.started_at = ctx.started_at
    result.completed_at = completed_at
    result.duration_ms = max(
        0,
        int(round((completed_at - ctx.started_at) * 1000)),
    )
    usage = token_usage or TokenUsage(
        input_tokens=result.input_tokens,
        cached_input_tokens=result.cached_input_tokens,
        cache_write_tokens=result.cache_write_tokens,
        output_tokens=result.output_tokens,
        reasoning_output_tokens=result.reasoning_output_tokens,
        input_tokens_present=result.input_tokens_present,
        cached_input_tokens_present=result.cached_input_tokens_present,
        cache_write_tokens_present=result.cache_write_tokens_present,
        output_tokens_present=result.output_tokens_present,
        reasoning_output_tokens_present=(
            result.reasoning_output_tokens_present
        ),
        source="result",
    )
    premium = (
        premium_requests
        if premium_requests is not None
        else (
            result.premium_requests
            if result.premium_requests_present
            else None
        )
    )
    provider_cost_usd = (
        usage.provider_cost_usd
        if usage.provider_cost_usd is not None
        else result.cost_usd
    )
    if ctx.usage_project_root is not None:
        try:
            from ...core.usage import (
                UsageLedger,
                build_usage_record,
                usage_recorded_event,
            )

            pricing_model, model_fallback_source = resolve_pricing_model(
                result.usage_model,
                ctx.options.model,
                None,
            )
            if model_fallback_source == "configured_default":
                # Traceability without spamming the durable event tape:
                # the provider response AND the request both lacked a
                # model, so the call was priced via the configured
                # default rather than a model the provider named.
                log.debug(
                    "codex model id empty for %s (call %s); pricing via "
                    "configured default %s "
                    "(raw_model_empty=True, model_fallback_source=%s)",
                    ctx.run_label, ctx.call_id, pricing_model, model_fallback_source,
                )
            record = build_usage_record(
                call_id=ctx.call_id,
                project_root=ctx.usage_project_root,
                mission_id=ctx.usage_mission_id,
                provider=backend._backend_name,
                model=pricing_model,
                run_label=ctx.run_label,
                started_at=ctx.started_at,
                completed_at=completed_at,
                status=(
                    status
                    if status in {"completed", "error", "denied"}
                    else "error"
                ),
                token_usage=usage,
                premium_requests=premium,
                total_nano_aiu=result.total_nano_aiu,
                provider_cost_usd=(
                    provider_cost_usd
                    if backend._backend_name == "opencode"
                    else None
                ),
                thread_id=result.thread_id,
                model_usage=result.model_usage,
                error=persisted_error,
            )
            appended = UsageLedger(
                ctx.usage_project_root,
                migrate_legacy=False,
            ).append(record)
            usage_record = record
            result.pricing_status = record.pricing_status
            result.cost_usd = record.cost_usd
            if appended:
                backend._log_agent_io(ctx.log_path, usage_recorded_event(record))
        except Exception:  # noqa: BLE001 — accounting must not break work
            log.exception("failed to persist usage record for %s", ctx.call_id)
    if ctx.cost_reservation is not None:
        try:
            if status == "denied":
                ctx.cost_reservation.release(
                    reason=persisted_error or "not_started"
                )
                backend._log_agent_io(ctx.log_path, {
                    "type": EventType.BUDGET_RESERVATION_RELEASED,
                    "reservation_id": ctx.cost_reservation.reservation_id,
                    "call_id": ctx.call_id,
                    "amount_usd": ctx.cost_reservation.amount_usd,
                    "reason": persisted_error or "not_started",
                })
            elif usage_record is not None:
                ctx.cost_reservation.settle(usage_record)
                backend._log_agent_io(ctx.log_path, {
                    "type": EventType.BUDGET_RESERVATION_SETTLED,
                    "reservation_id": ctx.cost_reservation.reservation_id,
                    "call_id": ctx.call_id,
                    "amount_usd": ctx.cost_reservation.amount_usd,
                    "cost_usd": usage_record.cost_usd,
                    "pricing_status": usage_record.pricing_status,
                })
            else:
                reason = persisted_error or "usage record was not persisted"
                ctx.cost_reservation.settle_unknown(reason=reason)
                backend._log_agent_io(ctx.log_path, {
                    "type": EventType.BUDGET_RESERVATION_SETTLED,
                    "reservation_id": ctx.cost_reservation.reservation_id,
                    "call_id": ctx.call_id,
                    "amount_usd": ctx.cost_reservation.amount_usd,
                    "cost_usd": None,
                    "pricing_status": "unknown",
                    "error": reason,
                })
        except Exception:  # noqa: BLE001 — metering must not break work
            log.exception("failed to settle cost admission for %s", ctx.call_id)
    if ctx.usage_project_root is not None:
        try:
            record_metric(
                metrics_root_for_project(ctx.usage_project_root),
                "provider.call",
                labels={
                    "provider": backend._backend_name,
                    "status": status,
                    "pricing_status": result.pricing_status or "unknown",
                },
                fields={
                    "call_id": ctx.call_id,
                    "mission_id": ctx.usage_mission_id,
                    "run_label": ctx.run_label,
                    "duration_ms": result.duration_ms,
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to record provider metric for %s", ctx.call_id)
    backend._close_io_context(ctx.call_id)
    return result


def finish_quota(
    ctx: "_ExecContext",
    *,
    success: bool,
    error_text: str = "",
    premium_requests: float = 0.0,
) -> None:
    backend = ctx.backend
    safe_error_text = redact_secrets_text(
        error_text,
        known_values=backend._known_secret_values,
    )
    if ctx.copilot_permit is not None:
        ctx.copilot_permit.finish(
            premium_requests=premium_requests,
            error_text=safe_error_text,
            success=success,
        )
    if ctx.codex_permit is not None:
        ctx.codex_permit.finish(success=success, error_text=safe_error_text)
    if ctx.event_permit is not None:
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.PROVIDER_REQUEST_COMPLETED,
            "provider": backend._backend_name,
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "success": bool(success),
            "error": (safe_error_text or "")[:500],
            "daily_calls": int(getattr(ctx.event_permit, "daily_calls", 0) or 0),
            "daily_cap": int(getattr(ctx.event_permit, "daily_cap", 0) or 0),
            "premium_requests": float(premium_requests or 0.0),
            "ts": time.time(),
        })
