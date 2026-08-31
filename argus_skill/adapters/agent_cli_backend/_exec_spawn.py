"""Provider subprocess execution for the agent-CLI backend.

:func:`spawn_and_finish` is entered only after successful admission.  It
handles all post-admission exit paths:

* CLI binary not found (``FileNotFoundError``)
* Subprocess raised an unexpected exception
* Result translation failure
* Happy path (successful call, failed call, pre-provider refusal)

On every path it calls :func:`._exec_finalize.finish_quota` and then
:func:`._exec_finalize.finalize_result`, preserving the original order of
quota settlement → I/O summary logging → usage accounting on each branch.

Note on ``capture_copilot_usage_cursor`` / ``read_copilot_usage_since``:
these names are module-level so that test monkey-patches targeting
``argus_skill.adapters.agent_cli_backend._exec_spawn.<name>`` work
correctly at call time.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ...core.models import RunnerResult
from ...core.runner_errors import result_has_pre_provider_refusal
from ...core.secret_guard import redact_secrets_text
from ...core.token_usage import extract_token_usage
from ...provider_integrations.authorization_retry import (
    AUTHORIZATION_RETRY_OWNER,
    BackendLoginRequired,
)
from ...provider_integrations.copilot_usage import (
    capture_copilot_usage_cursor,
    read_copilot_usage_since,
)
from ._exec_finalize import finalize_result, finish_quota
from ._io_log import _command_metadata
from ._io_log import raw_transcript_path as _raw_transcript_path
from ._result import _extract_copilot_premium_requests, looks_like_auth_failure

if TYPE_CHECKING:
    from ._exec_context import _ExecContext

log = logging.getLogger(__name__)


def log_start_record(backend: Any, ctx: "_ExecContext") -> None:
    """Record the call's opening in the history log, and — in ``full`` mode —
    its verbatim prompt in the raw transcript beside it.

    Split deliberately. ``events.jsonl`` is the authoritative history that the
    journal, mission view and campaign tally are projections of; the prompt is a
    debug artifact with no reader (the Web UI drops this event type, ``usage``
    takes only ``call_id``). Leaving it there made the history three times its
    own size — measured 63% of one project's 74.9 MB. ``call_id`` links the
    compact record to its verbatim copy.
    """
    start_row: dict[str, Any] = {
        "type": EventType.AGENT_IO_START,
        "io_kind": "start",
        "call_id": ctx.call_id,
        "run_label": ctx.run_label,
        "backend": backend._runner.backend,
        "model": ctx.options.model,
        "reasoning_effort": ctx.options.reasoning_effort,
        "working_dir": ctx.options.working_dir,
        "resume_thread_id": ctx.resume_thread_id,
        "ts": time.time(),
    }
    if ctx.io_mode == "compact":
        start_row["prompt_chars"] = len(ctx.prompt)
        backend._log_agent_io(ctx.log_path, start_row)
    else:
        # Full prompts belong in the bounded raw I/O transcript, not the
        # authoritative event history. Keep only size in events.jsonl;
        # agent_io.jsonl retains the verbatim copy under ring rotation.
        compact_row = dict(start_row)
        compact_row["prompt_chars"] = len(ctx.prompt)
        backend._log_agent_io(ctx.log_path, compact_row)

        start_row["prompt"] = ctx.prompt
        backend._log_agent_io(_raw_transcript_path(ctx.log_path), start_row)


def spawn_and_finish(ctx: "_ExecContext", cli_options: Any) -> RunnerResult:
    """Execute the provider subprocess and return a finalised ``RunnerResult``.

    Calls :func:`finish_quota` and :func:`finalize_result` on every exit
    path.  The happy path additionally calls ``_close_io_context`` a first
    time (before writing the I/O-complete summary row) so that the raw stream
    is flushed in the correct replay order; ``finalize_result`` calls it a
    second time as a no-op close.
    """
    backend = ctx.backend

    # ------------------------------------------------------------------ #
    # Log I/O start                                                        #
    # ------------------------------------------------------------------ #
    log_start_record(backend, ctx)

    # ------------------------------------------------------------------ #
    # Spawn subprocess                                                     #
    # ------------------------------------------------------------------ #
    copilot_usage_cursor = (
        capture_copilot_usage_cursor() if backend._is_copilot else None
    )
    try:
        cli_result = AUTHORIZATION_RETRY_OWNER.run_agent_cli(
            backend,
            prompt=ctx.prompt,
            resume_thread_id=ctx.resume_thread_id,
            options=cli_options,
            run_label=ctx.run_label,
        )
    except BackendLoginRequired as exc:
        backend._auth_failure_detected = True
        log.warning(
            "agent backend requires login after one coordinated auth replay "
            "(run_label=%s)",
            ctx.run_label,
        )
        finish_quota(ctx, error_text=str(exc), success=False)
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.AGENT_IO_ERROR,
            "io_kind": "error",
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "backend": getattr(backend._runner, "backend", ""),
            "error": str(exc),
            "ts": time.time(),
        })
        finalize_result(
            ctx,
            RunnerResult(
                exit_code=1,
                thread_id=ctx.resume_thread_id,
                fatal_error=exc.cause,
                stop_kind="permanent_error",
            ),
            status="error",
            error=str(exc),
        )
        raise
    except FileNotFoundError as exc:
        runner_name = str(
            getattr(backend._runner, "agent_bin", "")
            or getattr(backend._runner, "backend", "")
            or getattr(exc, "filename", "")
            or "runner"
        )
        failure = f"runner binary not found: {runner_name}: {exc}"
        log.error(
            "%s",
            failure,
        )
        finish_quota(ctx, error_text=str(exc), success=False)
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.AGENT_IO_ERROR,
            "io_kind": "error",
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "backend": getattr(backend._runner, "backend", ""),
            "error": failure,
            "ts": time.time(),
        })
        return finalize_result(
            ctx,
            RunnerResult(
                exit_code=127,
                fatal_error=failure,
                stop_kind="permanent_error",
            ),
            status="denied",
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — last-line safety net
        log.exception("codex runner raised")
        finish_quota(
            ctx,
            error_text=f"{type(exc).__name__}: {exc}",
            success=False,
        )
        backend._log_agent_io(ctx.log_path, {
            "type": EventType.AGENT_IO_ERROR,
            "io_kind": "error",
            "call_id": ctx.call_id,
            "run_label": ctx.run_label,
            "backend": getattr(backend._runner, "backend", ""),
            "error": f"{type(exc).__name__}: {exc}",
            "ts": time.time(),
        })
        return finalize_result(
            ctx,
            RunnerResult(
                exit_code=-1,
                fatal_error=f"{type(exc).__name__}: {exc}",
                stop_kind="backend_unavailable",
            ),
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    # ------------------------------------------------------------------ #
    # Read Copilot session-store usage and translate result                #
    # ------------------------------------------------------------------ #
    copilot_usage = read_copilot_usage_since(
        copilot_usage_cursor,
        session_id=(
            getattr(cli_result, "thread_id", None) or ctx.resume_thread_id
        ),
    )
    try:
        translated = backend._translate_result(
            cli_result,
            resume_thread_id=ctx.resume_thread_id,
            copilot_usage=copilot_usage,
        )
    except Exception as exc:  # noqa: BLE001
        finish_quota(
            ctx,
            error_text=f"result translation failed: {exc}",
            success=False,
        )
        raw_usage = extract_token_usage(
            getattr(cli_result, "json_events", None)
        )
        raw_premium, raw_premium_present = _extract_copilot_premium_requests(
            getattr(cli_result, "json_events", None)
        )
        return finalize_result(
            ctx,
            RunnerResult(
                exit_code=-1,
                thread_id=(
                    getattr(cli_result, "thread_id", None)
                    or ctx.resume_thread_id
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

    # ------------------------------------------------------------------ #
    # Failure detection + auth check                                       #
    # ------------------------------------------------------------------ #
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
            ctx.run_label,
            int(getattr(cli_result, "exit_code", 0) or 0),
        )

    finish_quota(
        ctx,
        premium_requests=translated.premium_requests,
        error_text=safe_failure_text,
        success=not failed,
    )

    # ------------------------------------------------------------------ #
    # Log I/O complete                                                     #
    # ------------------------------------------------------------------ #
    complete_row: dict[str, Any] = {
        "type": EventType.AGENT_IO_COMPLETE,
        "io_kind": "complete",
        "call_id": ctx.call_id,
        "run_label": ctx.run_label,
        "backend": getattr(backend._runner, "backend", ""),
        "model": translated.usage_model or ctx.options.model,
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
    backend._close_io_context(ctx.call_id)
    backend._log_agent_io(ctx.log_path, complete_row)
    return finalize_result(
        ctx,
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
