"""Provider execution orchestration for the agent CLI backend.

This module owns the single, large "make one provider call" state machine:
cost-reservation admission, provider quota permits, spawning the bundled
runner, translating its result, persisting the usage record, and settling
the reservation/quota/metrics bookkeeping — in that order, with every early
return still going through the same finalize path so accounting never gets
skipped on an error branch.

Kept as one function (mirroring the original monolithic
``AgentCliBackend.run_exec``) rather than atomized further: the steps share
a large amount of per-call local state (``call_id``, ``cost_reservation``,
the quota permits, …) that would otherwise have to be threaded through a
context object, and this is already the single-responsibility "execute one
provider call" concern — the surrounding modules handle the other
responsibilities (options translation, result translation, I/O logging).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ...core.codex_usage import TokenUsage, extract_token_usage
from ...core.copilot_usage import capture_copilot_usage_cursor, read_copilot_usage_since
from ...core.event_catalog import EventType
from ...core.metrics import metrics_root_for_project, record_metric
from ...core.models import RunnerOptions, RunnerResult
from ...core.runner_errors import result_has_pre_provider_refusal
from ...core.secret_guard import known_secret_values, redact_secrets_text
from ...core.stop_kinds import normalize_stop_kind, stop_kind_from_external_interrupt
from ._io_log import _command_metadata, _text_sha256
from ._options import _interrupt_reason, resolve_pricing_model
from ._result import (
    _extract_copilot_premium_requests,
    _reservation_denial_stop_kind,
    looks_like_auth_failure,
)

if TYPE_CHECKING:
    from ._core import AgentCliBackend

log = logging.getLogger(__name__)


def execute(
    backend: "AgentCliBackend",
    *,
    prompt: str,
    options: RunnerOptions,
    run_label: str,
    resume_thread_id: str | None = None,
) -> RunnerResult:
    backend._known_secret_values = known_secret_values()
    # Pin Codex's implicit config model before any accounting or execution.
    # The generated command, reservation, and settled usage record therefore
    # share one model id instead of independently guessing after the call.
    options = backend._resolve_execution_options(options)
    # Reset per-call: the flag is checked AFTER this call completes,
    # so stale True from a previous call cannot stick across missions.
    backend._auth_failure_detected = False
    call_id = uuid.uuid4().hex
    started_at = time.time()
    log_path = backend._agent_io_log_path(options)
    usage_project_root, usage_mission_id, usage_global_root = (
        backend._usage_context_snapshot()
    )
    if usage_project_root is None and log_path is not None:
        usage_project_root = log_path.parent
    cost_reservation = None
    io_context = backend._io_logger.start_call(
        call_id=call_id,
        run_label=run_label,
        log_path=log_path,
        model=options.model,
        prompt=prompt,
    )
    io_mode = io_context["mode"]

    def _finalize_result(
        result: RunnerResult,
        *,
        status: str,
        token_usage: TokenUsage | None = None,
        premium_requests: float | None = None,
        error: str = "",
    ) -> RunnerResult:
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
        result.call_id = call_id
        result.call_id_log_correlated = True
        result.stop_kind = normalize_stop_kind(result.stop_kind)
        result.thread_id = result.thread_id or resume_thread_id
        result.started_at = started_at
        result.completed_at = completed_at
        result.duration_ms = max(
            0,
            int(round((completed_at - started_at) * 1000)),
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
        if usage_project_root is not None:
            try:
                from ...core.usage import (
                    UsageLedger,
                    build_usage_record,
                    usage_recorded_event,
                )

                pricing_model, model_fallback_source = resolve_pricing_model(
                    result.usage_model,
                    options.model,
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
                        run_label, call_id, pricing_model, model_fallback_source,
                    )
                record = build_usage_record(
                    call_id=call_id,
                    project_root=usage_project_root,
                    mission_id=usage_mission_id,
                    provider=backend._backend_name,
                    model=pricing_model,
                    run_label=run_label,
                    started_at=started_at,
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
                    usage_project_root,
                    migrate_legacy=False,
                ).append(record)
                usage_record = record
                result.pricing_status = record.pricing_status
                result.cost_usd = record.cost_usd
                if appended:
                    backend._log_agent_io(log_path, usage_recorded_event(record))
            except Exception:  # noqa: BLE001 — accounting must not break work
                log.exception("failed to persist usage record for %s", call_id)
        if cost_reservation is not None:
            try:
                if status == "denied":
                    cost_reservation.release(
                        reason=persisted_error or "not_started"
                    )
                    backend._log_agent_io(log_path, {
                        "type": EventType.BUDGET_RESERVATION_RELEASED,
                        "reservation_id": cost_reservation.reservation_id,
                        "call_id": call_id,
                        "amount_usd": cost_reservation.amount_usd,
                        "reason": persisted_error or "not_started",
                    })
                elif usage_record is not None:
                    cost_reservation.settle(usage_record)
                    backend._log_agent_io(log_path, {
                        "type": EventType.BUDGET_RESERVATION_SETTLED,
                        "reservation_id": cost_reservation.reservation_id,
                        "call_id": call_id,
                        "amount_usd": cost_reservation.amount_usd,
                        "cost_usd": usage_record.cost_usd,
                        "pricing_status": usage_record.pricing_status,
                    })
                else:
                    reason = persisted_error or "usage record was not persisted"
                    cost_reservation.settle_unknown(reason=reason)
                    backend._log_agent_io(log_path, {
                        "type": EventType.BUDGET_RESERVATION_SETTLED,
                        "reservation_id": cost_reservation.reservation_id,
                        "call_id": call_id,
                        "amount_usd": cost_reservation.amount_usd,
                        "cost_usd": None,
                        "pricing_status": "unknown",
                        "error": reason,
                    })
            except Exception:  # noqa: BLE001 — metering must not break work
                log.exception("failed to settle cost admission for %s", call_id)
        if usage_project_root is not None:
            try:
                record_metric(
                    metrics_root_for_project(usage_project_root),
                    "provider.call",
                    labels={
                        "provider": backend._backend_name,
                        "status": status,
                        "pricing_status": result.pricing_status or "unknown",
                    },
                    fields={
                        "call_id": call_id,
                        "mission_id": usage_mission_id,
                        "run_label": run_label,
                        "duration_ms": result.duration_ms,
                        "cost_usd": result.cost_usd,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    },
                )
            except Exception:  # noqa: BLE001
                log.exception("failed to record provider metric for %s", call_id)
        backend._close_io_context(call_id)
        return result

    # Cost admission happens before the provider responds, so there is no
    # response model yet — attribute it to the request model, falling back to
    # the configured default (same rule as the settled usage record) so the
    # reservation ledger and its events never carry an empty codex model.
    reservation_model = resolve_pricing_model(
        None, options.model, None,
    )[0]
    try:
        from ...core.cost_control import (
            cost_control_enabled,
            reserve_call_budget,
        )

        if cost_control_enabled():
            cost_reservation, reserve_reason = reserve_call_budget(
                call_id=call_id,
                project_root=usage_project_root,
                mission_id=usage_mission_id,
                provider=backend._backend_name,
                model=reservation_model,
                run_label=run_label,
                global_root=usage_global_root,
            )
            if cost_reservation is None:
                backend._log_agent_io(log_path, {
                    "type": EventType.BUDGET_RESERVATION_DENIED,
                    "call_id": call_id,
                    "provider": backend._backend_name,
                    "model": reservation_model,
                    "run_label": run_label,
                    "reason": reserve_reason,
                })
                return _finalize_result(
                    RunnerResult(
                        exit_code=-1,
                        thread_id=resume_thread_id,
                        fatal_error=f"refused before start: {reserve_reason}",
                        stop_kind=_reservation_denial_stop_kind(reserve_reason),
                    ),
                    status="denied",
                    error=reserve_reason,
                )
            backend._log_agent_io(log_path, {
                "type": EventType.BUDGET_RESERVATION_CREATED,
                "reservation_id": cost_reservation.reservation_id,
                "call_id": call_id,
                "provider": backend._backend_name,
                "model": reservation_model,
                "run_label": run_label,
                "amount_usd": cost_reservation.amount_usd,
            })
    except Exception as exc:  # noqa: BLE001 — fail closed before provider spend
        reason = f"cost control unavailable: {type(exc).__name__}: {exc}"
        backend._log_agent_io(log_path, {
            "type": EventType.BUDGET_RESERVATION_DENIED,
            "call_id": call_id,
            "provider": backend._backend_name,
            "model": reservation_model,
            "run_label": run_label,
            "reason": reason,
        })
        return _finalize_result(
            RunnerResult(
                exit_code=-1,
                thread_id=resume_thread_id,
                fatal_error=f"refused before start: {reason}",
                stop_kind="backend_unavailable",
            ),
            status="denied",
            error=reason,
        )

    try:
        cli_options = backend._translate_options(options)
    except Exception as exc:  # noqa: BLE001 - release reservation on setup failure
        reason = f"runner option translation failed: {type(exc).__name__}: {exc}"
        return _finalize_result(
            RunnerResult(
                exit_code=-1,
                thread_id=resume_thread_id,
                fatal_error=f"refused before start: {reason}",
                stop_kind="permanent_error",
            ),
            status="denied",
            error=reason,
        )

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
        return _finalize_result(
            RunnerResult(
                exit_code=-1,
                thread_id=resume_thread_id,
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

        copilot_permit = acquire_copilot_permit(run_label)
        if not copilot_permit.allowed:
            reason = copilot_permit.reason
            release_denied_permit(copilot_permit)
            backend._log_agent_io(log_path, {
                "type": EventType.PROVIDER_REQUEST_DENIED,
                "provider": "copilot",
                "call_id": call_id,
                "run_label": run_label,
                "reason": reason,
                "ts": time.time(),
            })
            log.warning("Copilot call blocked before start (%s): %s", run_label, reason)
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind=normalize_stop_kind(copilot_permit.stop_kind),
                ),
                status="denied",
                error=reason,
            )
    elif backend._is_codex and not interrupted:
        from ...core.provider_quota import acquire_codex_permit

        codex_permit = acquire_codex_permit(run_label)
        if not codex_permit.allowed:
            reason = codex_permit.reason
            backend._log_agent_io(log_path, {
                "type": EventType.PROVIDER_REQUEST_DENIED,
                "provider": "codex",
                "call_id": call_id,
                "run_label": run_label,
                "reason": reason,
                "daily_calls": codex_permit.daily_calls,
                "daily_cap": codex_permit.daily_cap,
                "ts": time.time(),
            })
            log.warning("Codex call blocked before start (%s): %s", run_label, reason)
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind=normalize_stop_kind(codex_permit.stop_kind),
                ),
                status="denied",
                error=reason,
            )

    quota_permit = copilot_permit or codex_permit
    event_permit = (
        quota_permit
        if quota_permit is not None and bool(getattr(quota_permit, "guarded", True))
        else None
    )
    if event_permit is not None:
        backend._log_agent_io(log_path, {
            "type": EventType.PROVIDER_REQUEST_STARTED,
            "provider": backend._backend_name,
            "call_id": call_id,
            "run_label": run_label,
            "daily_calls": int(getattr(event_permit, "daily_calls", 0) or 0),
            "daily_cap": int(getattr(event_permit, "daily_cap", 0) or 0),
            "premium_requests_today": float(
                getattr(event_permit, "premium_requests_today", 0.0) or 0.0
            ),
            "premium_cap": float(getattr(event_permit, "premium_cap", 0.0) or 0.0),
            "ts": time.time(),
        })

    def _finish_quota(
        *,
        success: bool,
        error_text: str = "",
        premium_requests: float = 0.0,
    ) -> None:
        safe_error_text = redact_secrets_text(
            error_text,
            known_values=backend._known_secret_values,
        )
        if copilot_permit is not None:
            copilot_permit.finish(
                premium_requests=premium_requests,
                error_text=safe_error_text,
                success=success,
            )
        if codex_permit is not None:
            codex_permit.finish(success=success, error_text=safe_error_text)
        if event_permit is not None:
            backend._log_agent_io(log_path, {
                "type": EventType.PROVIDER_REQUEST_COMPLETED,
                "provider": backend._backend_name,
                "call_id": call_id,
                "run_label": run_label,
                "success": bool(success),
                "error": (safe_error_text or "")[:500],
                "daily_calls": int(getattr(event_permit, "daily_calls", 0) or 0),
                "daily_cap": int(getattr(event_permit, "daily_cap", 0) or 0),
                "premium_requests": float(premium_requests or 0.0),
                "ts": time.time(),
            })

    start_row: dict[str, Any] = {
        "type": EventType.AGENT_IO_START,
        "io_kind": "start",
        "call_id": call_id,
        "run_label": run_label,
        "backend": backend._runner.backend,
        "model": options.model,
        "reasoning_effort": options.reasoning_effort,
        "working_dir": options.working_dir,
        "resume_thread_id": resume_thread_id,
        "ts": time.time(),
    }
    if io_mode == "compact":
        start_row["prompt_chars"] = len(prompt)
        start_row["prompt_sha256"] = _text_sha256(prompt)
    else:
        start_row["prompt"] = prompt
    backend._log_agent_io(log_path, start_row)
    copilot_usage_cursor = (
        capture_copilot_usage_cursor() if backend._is_copilot else None
    )
    try:
        cli_result = backend._runner.run_exec(
            prompt=prompt,
            resume_thread_id=resume_thread_id,
            options=cli_options,
            run_label=run_label,
        )
    except FileNotFoundError as exc:
        log.exception("codex CLI binary not found")
        _finish_quota(error_text=str(exc), success=False)
        backend._log_agent_io(log_path, {
            "type": EventType.AGENT_IO_ERROR,
            "io_kind": "error",
            "call_id": call_id,
            "run_label": run_label,
            "backend": getattr(backend._runner, "backend", ""),
            "error": f"runner binary not found: {exc}",
            "ts": time.time(),
        })
        return _finalize_result(
            RunnerResult(
                exit_code=127,
                fatal_error=f"runner binary not found: {exc}",
                stop_kind="permanent_error",
            ),
            status="denied",
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — last-line safety net
        log.exception("codex runner raised")
        _finish_quota(
            error_text=f"{type(exc).__name__}: {exc}",
            success=False,
        )
        backend._log_agent_io(log_path, {
            "type": EventType.AGENT_IO_ERROR,
            "io_kind": "error",
            "call_id": call_id,
            "run_label": run_label,
            "backend": getattr(backend._runner, "backend", ""),
            "error": f"{type(exc).__name__}: {exc}",
            "ts": time.time(),
        })
        return _finalize_result(
            RunnerResult(
                exit_code=-1,
                fatal_error=f"{type(exc).__name__}: {exc}",
                stop_kind="backend_unavailable",
            ),
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    copilot_usage = read_copilot_usage_since(
        copilot_usage_cursor,
        session_id=(
            getattr(cli_result, "thread_id", None) or resume_thread_id
        ),
    )
    try:
        translated = backend._translate_result(
            cli_result,
            resume_thread_id=resume_thread_id,
            copilot_usage=copilot_usage,
        )
    except Exception as exc:  # noqa: BLE001
        _finish_quota(
            error_text=f"result translation failed: {exc}",
            success=False,
        )
        raw_usage = extract_token_usage(
            getattr(cli_result, "json_events", None)
        )
        raw_premium, raw_premium_present = _extract_copilot_premium_requests(
            getattr(cli_result, "json_events", None)
        )
        return _finalize_result(
            RunnerResult(
                exit_code=-1,
                thread_id=(
                    getattr(cli_result, "thread_id", None)
                    or resume_thread_id
                ),
                fatal_error=f"result translation failed: {exc}",
                stop_kind="backend_unavailable",
                usage_model=(
                    copilot_usage.model if copilot_usage is not None else ""
                ),
                total_nano_aiu=(
                    copilot_usage.total_nano_aiu
                    if copilot_usage is not None
                    else None
                ),
                model_usage=(
                    list(copilot_usage.model_usage)
                    if copilot_usage is not None
                    else []
                ),
            ),
            status="error",
            token_usage=raw_usage,
            premium_requests=raw_premium if raw_premium_present else None,
            error=f"result translation failed: {exc}",
        )

    failed = bool(
        getattr(cli_result, "turn_failed", False)
        or getattr(cli_result, "fatal_error", None)
        or int(getattr(cli_result, "exit_code", 0) or 0) != 0
    )
    stderr_lines = list(getattr(cli_result, "stderr_lines", None) or [])
    fatal_error = str(getattr(cli_result, "fatal_error", "") or "")
    failure_text = "\n".join([fatal_error, *map(str, stderr_lines)]).strip()
    safe_failure_text = redact_secrets_text(
        failure_text,
        known_values=backend._known_secret_values,
    )
    pre_provider_refusal = bool(
        result_has_pre_provider_refusal(cli_result)
        and translated.total_nano_aiu is None
        and not translated.model_usage
        and not translated.premium_requests_present
        and not any((
            translated.input_tokens_present,
            translated.cached_input_tokens_present,
            translated.cache_write_tokens_present,
            translated.output_tokens_present,
            translated.reasoning_output_tokens_present,
        ))
    )

    # Detect auth/policy failures even when Copilot exits 0 but reports
    # turn_failed=true. Policy denial previously looked "successful" at the
    # process level, so every daemon kept retrying a blocked account.
    if failed and looks_like_auth_failure([failure_text]):
        backend._auth_failure_detected = True
        log.warning(
            "agent backend reported auth/policy failure "
            "(run_label=%s, exit_code=%d)",
            run_label,
            int(getattr(cli_result, "exit_code", 0) or 0),
        )

    _finish_quota(
        premium_requests=translated.premium_requests,
        error_text=safe_failure_text,
        success=not failed,
    )

    complete_row: dict[str, Any] = {
        "type": EventType.AGENT_IO_COMPLETE,
        "io_kind": "complete",
        "call_id": call_id,
        "run_label": run_label,
        "backend": getattr(backend._runner, "backend", ""),
        "model": translated.usage_model or options.model,
        "exit_code": getattr(cli_result, "exit_code", None),
        "thread_id": getattr(cli_result, "thread_id", None),
        "turn_completed": getattr(cli_result, "turn_completed", None),
        "turn_failed": getattr(cli_result, "turn_failed", None),
        "fatal_error": redact_secrets_text(
            str(getattr(cli_result, "fatal_error", "") or ""),
            known_values=backend._known_secret_values,
        ) or None,
        "tool_activity_observed": bool(
            getattr(cli_result, "tool_activity_observed", False)
        ),
        "input_tokens": translated.input_tokens,
        "cached_input_tokens": translated.cached_input_tokens,
        "cache_write_tokens": translated.cache_write_tokens,
        "output_tokens": translated.output_tokens,
        "reasoning_output_tokens": translated.reasoning_output_tokens,
        "premium_requests": (
            translated.premium_requests
            if translated.premium_requests_present
            else None
        ),
        "premium_requests_present": translated.premium_requests_present,
        "total_nano_aiu": translated.total_nano_aiu,
        "usage_model": translated.usage_model,
        "ts": time.time(),
    }
    messages = list(getattr(cli_result, "agent_messages", []) or [])
    retained_stdout = list(getattr(cli_result, "stdout_lines", []) or [])
    retained_stderr = list(getattr(cli_result, "stderr_lines", []) or [])
    retained_events = list(getattr(cli_result, "json_events", []) or [])
    stdout_count = int(
        getattr(cli_result, "stdout_line_count", 0)
        or len(retained_stdout)
    )
    stderr_count = int(
        getattr(cli_result, "stderr_line_count", 0)
        or len(retained_stderr)
    )
    event_count = int(
        getattr(cli_result, "json_event_count", 0)
        or len(retained_events)
    )
    complete_row.update({
        "agent_message_count": len(messages),
        "agent_message_chars": sum(len(str(message)) for message in messages),
        "last_agent_message_sha256": (
            _text_sha256(messages[-1]) if messages else None
        ),
        "stdout_line_count": stdout_count,
        "stderr_line_count": stderr_count,
        "json_event_count": event_count,
        "stdout_capture_truncated": stdout_count > len(retained_stdout),
        "stderr_capture_truncated": stderr_count > len(retained_stderr),
        "json_event_capture_truncated": event_count > len(retained_events),
        "command": _command_metadata(
            getattr(cli_result, "command", []) or []
        ),
    })
    # Full raw frames are already persisted exactly once. Flush and close
    # that stream before writing the summary so replay order is start →
    # stream* → complete → usage.
    backend._close_io_context(call_id)
    backend._log_agent_io(log_path, complete_row)
    return _finalize_result(
        translated,
        status=(
            "denied"
            if pre_provider_refusal
            else "error"
            if failed
            else "completed"
        ),
        error=safe_failure_text,
    )
