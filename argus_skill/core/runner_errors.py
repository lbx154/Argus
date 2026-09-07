"""Structured recognition of runner failures that happen before a model turn."""

from __future__ import annotations

import re
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
)
_EXECUTION_HOST_STARTUP_PREFIX = (
    "code mode is unavailable because failed to spawn code-mode host "
)


def terminal_failure_diagnostic(result: Any) -> str:
    """Select one current diagnostic; stderr remains a separate history.

    Concrete terminal receipts win. Generic process-exit receipts may use the
    latest startup diagnostic only when no model/tool progress was observed.
    This also supports older/external runners without diagnostic provenance.
    Never combine old stderr with the current failure for control decisions.
    """
    fatal = str(getattr(result, "fatal_error", None) or "").strip()
    failed = bool(getattr(result, "turn_failed", False)
                  or int(getattr(result, "exit_code", 0) or 0) != 0 or fatal)
    if getattr(result, "turn_completed", False) and not getattr(result, "turn_failed", False) and int(getattr(result, "exit_code", 0) or 0) == 0:
        return ""
    generic = (not fatal or fatal.casefold() == "process exited" or fatal == "Agent CLI exited without completing a model turn."
               or bool(re.fullmatch(r"Process exited with code -?\d+ before turn completion\.", fatal)))
    if not generic:
        return fatal
    progress = bool(
        getattr(result, "provider_turns", 0)
        or getattr(result, "model_progress_observed", False)
        or getattr(result, "tool_activity_observed", False)
        or getattr(result, "agent_messages", None)
        or any(e.get("type") in {"turn.started", "turn.completed", "turn.failed",
                                 "item.started", "item.updated", "item.completed"}
               for e in (getattr(result, "json_events", None) or []) if isinstance(e, dict))
    )
    if progress:
        return (fatal or "Backend exited after progress without a terminal diagnostic.") if failed else ""
    # Startup has no model turn to recover within. The last nonempty line is
    # the best available evidence; do not search backwards for a desired code.
    for line in reversed(getattr(result, "stderr_lines", None) or []):
        if str(line).strip():
            text = str(line).strip()
            # Some providers reject startup with exit 0 and no turn receipt.
            return text if failed or is_pre_provider_refusal_error(text) else ""
    return fatal


def is_execution_host_startup_error(value: object) -> bool:
    """Recognize the Codex runtime receipt for an unavailable execution host.

    Callers must supply a trusted error receipt, never assistant prose or tool
    output. Requiring the complete diagnostic prefix also avoids interpreting
    discussions or quoted examples of a missing host as a startup failure.
    """
    lowered = str(value or "").strip().casefold()
    return lowered.startswith(_EXECUTION_HOST_STARTUP_PREFIX) and any(
        marker in lowered[len(_EXECUTION_HOST_STARTUP_PREFIX):]
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
    return is_missing_resume_target_error(terminal_failure_diagnostic(result))


def result_has_unrecoverable_resume_state(result: Any) -> bool:
    return is_unrecoverable_resume_error(terminal_failure_diagnostic(result))


def result_has_pre_provider_refusal(result: Any) -> bool:
    return is_pre_provider_refusal_error(terminal_failure_diagnostic(result))


__all__ = [
    "terminal_failure_diagnostic",
    "is_execution_host_startup_error",
    "is_missing_resume_target_error",
    "is_model_catalog_startup_error",
    "is_pre_provider_refusal_error",
    "is_unrecoverable_resume_error",
    "result_has_missing_resume_target",
    "result_has_pre_provider_refusal",
    "result_has_unrecoverable_resume_state",
]
