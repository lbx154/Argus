"""Structured recognition of runner failures that happen before a model turn."""

from __future__ import annotations

from typing import Any

_MISSING_RESUME_TARGET = "No session, task, or name matched"
_REFUSED_BEFORE_START = "refused before start:"
_ENCRYPTED_CONTENT = "encrypted content"
_ENCRYPTED_CONTENT_FAILURES = (
    "could not be verified",
    "could not be decrypted or parsed",
)
_PRE_PROVIDER_REFUSALS = (
    "copilot wrapper: real copilot cli binary not found",
    "no authentication information found",
    "token refresh failed: 401",
    # Copilot startup entitlement checks can exit zero without starting a model
    # turn. Callers must still require absent usage before treating these free.
    "access denied by policy settings",
    "subscription does not include this feature",
    "required policies have not been enabled",
)
_MODEL_CATALOG_FAILURES = (
    "error: failed to load models",
    "copilot could not retrieve the list of available models",
    "model is not supported when using codex with a chatgpt account",
)
_EXECUTION_HOST_STARTUP_PREFIX = "code mode is unavailable because failed to spawn code-mode host "


def is_execution_host_startup_error(value: object) -> bool:
    """Recognize the Codex runtime receipt for an unavailable execution host.

    Callers must supply a trusted error receipt, never assistant prose or tool
    output. Requiring the complete diagnostic prefix also avoids interpreting
    discussions or quoted examples of a missing host as a startup failure.
    """
    lowered = str(value or "").strip().casefold()
    return lowered.startswith(_EXECUTION_HOST_STARTUP_PREFIX) and any(
        marker in lowered[len(_EXECUTION_HOST_STARTUP_PREFIX) :]
        for marker in (
            "host executable was not found",
            "startup failure",
            "fail closed",
        )
    )


def is_missing_resume_target_error(value: object) -> bool:
    return _MISSING_RESUME_TARGET in str(value or "")


def is_pre_provider_refusal_error(value: object) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return (
        is_missing_resume_target_error(text)
        or _REFUSED_BEFORE_START in lowered
        or any(marker in lowered for marker in _PRE_PROVIDER_REFUSALS)
        or is_model_catalog_startup_error(text)
    )


def is_provider_access_startup_error(value: object) -> bool:
    """Provider refused before any turn: no model catalog, or a policy denial.

    Both mean the account, subscription, or session behind the CLI is not
    usable right now, not that this mission or its model choice is wrong.
    """
    text = str(value or "")
    lowered = text.lower()
    return is_model_catalog_startup_error(text) or any(
        marker in lowered for marker in _PRE_PROVIDER_REFUSALS
    )


def is_model_catalog_startup_error(value: object) -> bool:
    """Recognize model discovery failure, never a generic HTTP/turn failure."""
    lowered = str(value or "").lower()
    return any(marker in lowered for marker in _MODEL_CATALOG_FAILURES)


def is_unrecoverable_resume_error(value: object) -> bool:
    """Return whether a persisted runner thread can no longer be resumed."""
    text = str(value or "")
    lowered = text.lower()
    return is_missing_resume_target_error(text) or (
        _ENCRYPTED_CONTENT in lowered
        and any(marker in lowered for marker in _ENCRYPTED_CONTENT_FAILURES)
    )


def result_has_missing_resume_target(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_missing_resume_target_error("\n".join(map(str, parts)))


def result_has_unrecoverable_resume_state(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_unrecoverable_resume_error("\n".join(map(str, parts)))


def result_has_pre_provider_refusal(result: Any) -> bool:
    parts = [
        getattr(result, "fatal_error", ""),
        *(getattr(result, "stderr_lines", None) or []),
    ]
    return is_pre_provider_refusal_error("\n".join(map(str, parts)))


__all__ = [
    "is_execution_host_startup_error",
    "is_missing_resume_target_error",
    "is_model_catalog_startup_error",
    "is_pre_provider_refusal_error",
    "is_unrecoverable_resume_error",
    "result_has_missing_resume_target",
    "result_has_pre_provider_refusal",
    "result_has_unrecoverable_resume_state",
]


def is_copilot_context_parser_error(value: object) -> bool:
    """Exact runner-wrapped parser diagnostic; text alone is not authority."""
    prefix = (
        "Process exited with code 1 before turn completion.\nerror: unknown option '--context'\n"
    )
    suffix = "Try 'copilot --help' for more information."
    return str(value or "").strip() in (
        prefix + "(Did you mean --connect?)\n\n" + suffix,
        prefix + "\n" + suffix,
    )


def is_copilot_context_parser_refusal(
    error: object,
    *,
    provider: str,
    call_id: str,
    run_label: str,
    status: str,
    thread_id: object,
    source: str,
    receipt: dict[str, Any] | None,
) -> bool:
    """Use only host-generated agent.io.complete, never model/tool JSON.

    A parser diagnostic is positive startup evidence only when the matching
    process receipt confirms an unsuccessful, silent pre-turn CLI invocation.
    Usage accounting must independently reject every observed usage field.
    """
    if not is_copilot_context_parser_error(error) or not receipt:
        return False
    command = receipt.get("command")
    return bool(
        provider == "copilot"
        and status == "error"
        and source == "run_exec"
        and not thread_id
        and call_id
        and receipt.get("type") == "agent.io.complete"
        and receipt.get("backend") == provider
        and receipt.get("call_id") == call_id
        and receipt.get("run_label") == run_label
        and receipt.get("exit_code") == 1
        and receipt.get("turn_failed") is True
        and receipt.get("turn_completed") is False
        and receipt.get("thread_id") is None
        and receipt.get("fatal_error") == "Process exited with code 1 before turn completion."
        and receipt.get("tool_activity_observed") is False
        and all(
            receipt.get(key) == 0
            for key in ("agent_message_count", "stdout_line_count", "json_event_count")
        )
        # Older receipts store zero token placeholders without presence bits.
        # Nonzero receipt usage still contradicts a missing-usage ledger row;
        # explicit premium/billing values (including zero) are always evidence.
        and all(
            receipt.get(key) in (None, 0)
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            )
        )
        and not receipt.get("premium_requests_present")
        and all(
            receipt.get(key) is None
            for key in (
                "premium_requests",
                "total_nano_aiu",
                "cost_usd",
                "provider_cost_usd",
                "premium_request_cost_usd",
            )
        )
        and not receipt.get("model_usage")
        and isinstance(command, list)
        and any(command[i : i + 2] == ["--context", "default"] for i in range(1, len(command) - 1))
    )


def is_local_startup_parser_error(value: object) -> bool:
    return is_copilot_context_parser_error(value) or str(value or "").strip() == (
        "Process exited with code 1 before turn completion.\n"
        "Not inside a trusted directory and --skip-git-repo-check was not specified."
    )


def is_local_startup_refusal(error: object, **context) -> bool:
    """Only a matching, silent CLI completion proves a zero-provider refusal."""
    if is_copilot_context_parser_refusal(error, **context):
        return True
    r = context.get("receipt") or {}
    command = r.get("command") or []
    return bool(
        is_local_startup_parser_error(error)
        and not is_copilot_context_parser_error(error)
        and context.get("provider") == "codex"
        and context.get("status") == "error"
        and context.get("source") == "run_exec"
        and not context.get("thread_id")
        and context.get("call_id")
        and r.get("call_id") == context["call_id"]
        and r.get("run_label") == context.get("run_label")
        and r.get("backend") == "codex"
        and r.get("type") == "agent.io.complete"
        and r.get("exit_code") == 1
        and r.get("turn_failed") is True
        and r.get("turn_completed") is False
        and r.get("thread_id") is None
        and r.get("tool_activity_observed") is False
        and r.get("fatal_error") == "Process exited with code 1 before turn completion."
        and all(
            r.get(k) == 0 for k in ("agent_message_count", "stdout_line_count", "json_event_count")
        )
        and all(
            r.get(k) in (None, 0)
            for k in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_output_tokens",
            )
        )
        and not r.get("premium_requests_present")
        and not r.get("model_usage")
        and all(
            r.get(k) is None
            for k in (
                "premium_requests",
                "total_nano_aiu",
                "cost_usd",
                "provider_cost_usd",
                "premium_request_cost_usd",
            )
        )
        and isinstance(command, list)
        and len(command) > 1
        and command[1] == "exec"
        and "--skip-git-repo-check" not in command
    )
