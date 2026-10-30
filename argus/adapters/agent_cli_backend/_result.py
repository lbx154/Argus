"""Result and usage normalization: stop-kind classification plus translating
the bundled runner's raw result into Argus's ``RunnerResult``.

This module owns everything about turning a provider's raw response into
Argus's own shapes: auth-failure/stop-kind pattern classification,
copilot premium-request extraction, the thread-cumulative usage
de-cumulation (:class:`UsageAccumulator`), and :func:`translate_result`
itself.
"""
from __future__ import annotations

import re
import threading
from dataclasses import replace
from typing import Any

from ...agent_cli._env import _incomplete_turn_error
from ...core.http_status import has_http_status
from ...core.models import RunnerResult
from ...core.runner_errors import (
    is_execution_host_startup_error,
    is_generic_exit_receipt,
    is_model_catalog_startup_error,
    terminal_failure_diagnostic,
)
from ...core.runner_receipts import is_provider_background_wait_receipt
from ...core.stop_kinds import (
    StopKind,
    normalize_stop_kind,
    stop_kind_from_external_interrupt,
)
from ...core.token_usage import COUNT_FIELDS, TokenUsage, extract_token_usage, summed_usage
from ...provider_integrations.copilot_usage import CopilotCallUsage

_AUTH_FAILURE_PATTERNS: tuple[str, ...] = (
    "unauthorized",
    "expired token",
    "invalid token",
    "authentication failed",
    "access denied by policy settings",
    "subscription does not include this feature",
    "required policies have not been enabled",
    "please run `codex login`",
    "codex login",
    "invalid api key",
    "no api key",
    "missing credentials",
    "oauth refresh failed",
    "token refresh failed",
    "no models available",
    "use /login",
)
_RECOVERABLE_RECONNECT_RE = re.compile(r"^reconnecting\.\.\.\s*(\d+)/(\d+)\b")
_PROVIDER_COOLDOWN_PATTERNS = (
    "rate limit",
    "rate-limit",
    "too many requests",
    "retry after",
    "retry-after",
    "circuit open",
    "cooldown",
)
_PROVIDER_FENCE_PATTERNS = (
    "error_max_budget_usd",
    "max budget usd",
    "max-budget-usd",
    "provider budget limit",
)
_TRANSIENT_ERROR_PATTERNS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "stream disconnected",
    "wall-clock limit reached",
    "service unavailable",
)


def _raw_backend_stop_kind(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> StopKind | None:
    fatal = str(fatal_error or "").strip()
    if not fatal and int(exit_code or 0) == 0:
        return None
    low = fatal.casefold()
    if (
        is_provider_background_wait_receipt(fatal)
        or is_execution_host_startup_error(fatal)
    ):
        # Local terminal receipts outrank recovered failures in captured stderr.
        return "backend_unavailable"
    if low.startswith("external interrupt:"):
        interrupt_kind = stop_kind_from_external_interrupt(fatal)
        if interrupt_kind is not None:
            return interrupt_kind
    from ...core.hosting import hosting

    if kind := hosting().failure_kind(fatal):
        return kind
    if has_http_status(low, {402}) or any(pattern in low for pattern in _PROVIDER_FENCE_PATTERNS):
        return "provider_fence"
    if has_http_status(low, {429}) or any(
        pattern in low for pattern in _PROVIDER_COOLDOWN_PATTERNS
    ):
        return "provider_cooldown"
    if looks_like_auth_failure([low]):
        return "permanent_error"
    if has_http_status(low, {502, 503, 504}) or any(
        pattern in low for pattern in _TRANSIENT_ERROR_PATTERNS
    ):
        return "transient_error"
    if low.startswith("refused before start:"):
        return "permanent_error"
    return "backend_unavailable"


def looks_like_auth_failure(stderr_lines) -> bool:  # noqa: ANN001
    """Return True iff any stderr line matches a known auth-failure pattern.

    Used by the lifetime daemon to detect "codex token expired overnight"
    without crashing — the daemon logs a warning, finishes the current
    mission as failed, and keeps polling. Operators see the warning in
    the journal / stderr and re-authenticate at their leisure.
    """
    if not stderr_lines:
        return False
    for raw in stderr_lines:
        if not raw:
            continue
        if (
            is_provider_background_wait_receipt(raw)
            or is_execution_host_startup_error(raw)
        ):
            continue
        low = str(raw).lower()
        if has_http_status(low, {401, 403}):
            return True
        for pat in _AUTH_FAILURE_PATTERNS:
            if pat in low:
                return True
    return False


def _normalize_fatal_error(fatal_error: str | None) -> str | None:
    if _looks_like_recoverable_reconnect(fatal_error):
        return None
    return fatal_error


def _looks_like_recoverable_reconnect(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    match = _RECOVERABLE_RECONNECT_RE.search(low)
    return bool(match)


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _extract_copilot_premium_requests(
    events: list[dict[str, Any]] | None,
) -> tuple[float, bool]:
    if not events:
        return 0.0, False
    last = 0.0
    present = False
    for event in events:
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if usage is None:
            continue
        raw = usage.get("premiumRequests")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            last = float(raw)
            present = True
    return last, present


class UsageAccumulator:
    """Per-``AgentCliBackend`` thread-cumulative usage de-cumulator.

    Codex reports lifecycle-cumulative token totals and Copilot reports a
    session-cumulative ``premiumRequests`` total; both need to be converted
    into this call's delta by remembering the previous total seen per
    resumed thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread_usage_totals: dict[str, tuple[int, int, int, int]] = {}
        # 中文：copilot 的 premiumRequests 是会话累计值；按线程存上次累计，只计本次增量。
        self._thread_premium_totals: dict[str, float] = {}

    def usage_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_totals: tuple[int, ...],
        resume_baseline_unknown: bool = False,
    ) -> tuple[int, ...] | None:
        """Convert Codex lifecycle-cumulative usage into this call's delta.

        A resumed thread with no baseline in memory or in the ledger, or whose
        totals went down, is unresolved (``None``): the totals would bill its
        earlier turns again (2026-10-27: a restarted daemon's first Planner
        turn was recorded as 9.94M tokens and $7.63 for 625K).
        """
        if not thread_id:
            return raw_totals

        from ...core.session_ledger import remember_usage_totals, usage_totals

        with self._lock:
            previous = self._thread_usage_totals.get(thread_id)
            self._thread_usage_totals[thread_id] = raw_totals
        if previous is None:
            # Another process (or an earlier backend object) may have run
            # this thread's earlier turns; its totals are on disk.
            recorded = usage_totals(thread_id)
            previous = recorded if recorded and len(recorded) == len(raw_totals) else None
        remember_usage_totals(thread_id, raw_totals)

        if previous is None:
            return None if resume_baseline_unknown and any(raw_totals) else raw_totals
        deltas = tuple(now - before for now, before in zip(raw_totals, previous))
        return None if any(delta < 0 for delta in deltas) else deltas

    def premium_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_total: float,
        resume_baseline_unknown: bool = False,
    ) -> float | None:
        """Convert copilot's session-cumulative premiumRequests into this call's
        delta. A resumed thread without an in-memory baseline is unresolved for
        its first call after restart; charging the cumulative total would bill
        the earlier turns again. Mirrors ``usage_delta_for_thread`` otherwise.
        把 copilot 会话累计的 premiumRequests 转成本次调用的增量（标量版）。"""
        current = max(0.0, float(raw_total))
        if not thread_id:
            return current

        with self._lock:
            previous = self._thread_premium_totals.get(thread_id)
            self._thread_premium_totals[thread_id] = current

        if previous is None:
            if resume_baseline_unknown and current > 0.0:
                return None
            return current
        delta = current - previous
        if delta < 0.0:
            # Cumulative counter reset (new session on the same id) — charge the
            # current total as a fresh delta rather than a negative credit.
            return current
        return delta


def translate_result(
    cli_result: Any,
    *,
    resume_thread_id: str | None,
    copilot_usage: CopilotCallUsage | None,
    usage_accumulator: UsageAccumulator,
    codex_requests: tuple[TokenUsage, ...] = (),
    is_codex: bool = False,
) -> RunnerResult:
    """Translate the bundled runner's raw result into a ``RunnerResult``.

    Only codex reports a thread's lifetime totals; other CLIs report the run's
    own usage, which needs no baseline. ``codex_requests`` are the call's model
    requests codex recorded in its session files; when present they, not the
    thread totals, are its usage.
    """
    authoritative_usage_model = str(
        getattr(cli_result, "usage_model", "") or ""
    ).strip()
    thread_id = cli_result.thread_id or resume_thread_id
    # A new thread's totals are this call's own; a resumed thread's are not.
    resumed = bool(resume_thread_id and thread_id == resume_thread_id)
    if copilot_usage is not None:
        usage = TokenUsage(
            input_tokens=copilot_usage.input_tokens or 0,
            cached_input_tokens=copilot_usage.cache_read_tokens or 0,
            cache_write_tokens=copilot_usage.cache_write_tokens or 0,
            output_tokens=copilot_usage.output_tokens or 0,
            reasoning_output_tokens=copilot_usage.reasoning_tokens or 0,
            input_tokens_present=copilot_usage.input_tokens is not None,
            cached_input_tokens_present=(
                copilot_usage.cache_read_tokens is not None
            ),
            cache_write_tokens_present=(
                copilot_usage.cache_write_tokens is not None
            ),
            output_tokens_present=copilot_usage.output_tokens is not None,
            reasoning_output_tokens_present=(
                copilot_usage.reasoning_tokens is not None
            ),
            source="copilot_session_store",
        )
    else:
        usage = extract_token_usage(
            getattr(cli_result, "json_events", None)
        )
        if is_codex and usage.source == "cumulative":
            # The baseline stays current even when codex's request rows settle the call.
            delta = usage_accumulator.usage_delta_for_thread(
                thread_id=thread_id,
                raw_totals=tuple(getattr(usage, name) for name in COUNT_FIELDS),
                resume_baseline_unknown=resumed,
            )
            usage = (
                TokenUsage()
                if delta is None
                else replace(usage, **dict(zip(COUNT_FIELDS, delta)))
            )
        if codex_requests:
            usage = summed_usage(codex_requests, source="codex_session")
    raw_premium, premium_requests_present = _extract_copilot_premium_requests(
        getattr(cli_result, "json_events", None)
    )
    premium_requests = (
        usage_accumulator.premium_delta_for_thread(
            thread_id=thread_id,
            raw_total=raw_premium,
            resume_baseline_unknown=resumed,
        )
        if premium_requests_present
        else None
    )
    premium_requests_present = (
        premium_requests_present and premium_requests is not None
    )
    usage_model = authoritative_usage_model or (
        copilot_usage.model if copilot_usage is not None else ""
    )
    model_usage = (
        list(copilot_usage.model_usage)
        if copilot_usage is not None
        else []
    )
    if authoritative_usage_model:
        model_usage = [
            {**row, "model": authoritative_usage_model}
            for row in model_usage
        ]
    # One current terminal diagnostic drives every control decision below;
    # the recovered stderr history stays in ``stderr_lines`` and is never
    # combined with it. The failure record itself keeps the runner's fuller
    # account (its exit receipt followed by the CLI's last stderr lines).
    failure_diagnostic = terminal_failure_diagnostic(cli_result)
    fatal_error = _normalize_fatal_error(cli_result.fatal_error)
    if (
        getattr(cli_result, "turn_failed", False)
        and not fatal_error
    ):
        fatal_error = _incomplete_turn_error(
            getattr(cli_result, "stderr_lines", None) or []
        )
    stderr_diagnostic = "\n".join(
        map(str, getattr(cli_result, "stderr_lines", None) or [])
    ).strip()
    if (
        failure_diagnostic
        and is_generic_exit_receipt(getattr(cli_result, "fatal_error", None))
        and is_model_catalog_startup_error(stderr_diagnostic)
        and not is_model_catalog_startup_error(fatal_error)
    ):
        # CLI startup can provide only a generic terminal receipt while
        # stderr explains that model discovery failed before any turn. A
        # concrete receipt (an operator stop, a provider error) is never
        # replaced by that history.
        fatal_error = stderr_diagnostic
    stop_kind = normalize_stop_kind(
        getattr(cli_result, "stop_kind", None)
    ) or _raw_backend_stop_kind(
        fatal_error=failure_diagnostic,
        exit_code=cli_result.exit_code,
    )
    # Preserve the established Manager timeout backoff for its final explicit
    # reconnect/429 notice. This narrow scheduling hint must not participate
    # in auth replay, overwrite the receipt, or override an operator stop.
    if (
        stop_kind == "transient_error"
        and failure_diagnostic.casefold().startswith(
            "external interrupt: manager turn wall-clock limit reached"
        )
    ):
        latest = next((
            str(line).strip()
            for line in reversed(getattr(cli_result, "stderr_lines", None) or [])
            if str(line).strip()
        ), "")
        if latest.casefold().startswith("reconnecting") and has_http_status(latest, {429}):
            stop_kind = "provider_cooldown"
    return RunnerResult(
        exit_code=cli_result.exit_code,
        agent_messages=list(cli_result.agent_messages or []),
        stdout_lines=list(cli_result.stdout_lines or []),
        stderr_lines=list(cli_result.stderr_lines or []),
        thread_id=thread_id,
        fatal_error=fatal_error,
        stop_kind=stop_kind,
        **{name: getattr(usage, name) for name in COUNT_FIELDS},
        **{f"{name}_present": getattr(usage, f"{name}_present") for name in COUNT_FIELDS},
        premium_requests=premium_requests or 0.0,
        premium_requests_present=premium_requests_present,
        usage_model=usage_model,
        cost_usd=usage.provider_cost_usd,
        total_nano_aiu=(
            copilot_usage.total_nano_aiu
            if copilot_usage is not None
            else None
        ),
        model_usage=model_usage,
        tool_activity_observed=bool(
            getattr(cli_result, "tool_activity_observed", False)
        ),
        orphan_process_group_id=int(
            getattr(cli_result, "orphan_process_group_id", 0) or 0
        ),
        orphan_process_group_cleanup_succeeded=bool(
            getattr(
                cli_result,
                "orphan_process_group_cleanup_succeeded",
                False,
            )
        ),
    )
