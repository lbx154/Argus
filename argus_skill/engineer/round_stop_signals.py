"""Stop-kind / backend-failure classification for the Engineer round loop.

Groups the pattern-matching helpers that turn a raw ``RunnerResult.fatal_error``
string (or an already-normalized ``stop_kind``) into a semantic classification —
poisoned session, backend transport failure, model misconfiguration, recoverable
reconnect notice, effective-progress-timeout, compaction thrash, daemon-stop /
operator-abort interrupt — plus the ``ReviewDecision`` builders the round loop
uses to fabricate a skipped-review verdict for each of those non-review-worthy
stop conditions. This is a leaf module: it has no dependency on ``runner.py`` so
the round-loop phase mixins can import it directly without a cycle; ``runner.py``
re-imports these same names to keep its historical public surface unchanged.
"""
from __future__ import annotations

import json
import re

from ..core.models import ReviewDecision, RunnerResult
from ..core.stop_kinds import normalize_stop_kind

_SUCCESS_ITEM_STATUSES: tuple[str, ...] = (
    "completed",
    "succeeded",
    "success",
    "ok",
    "applied",
)
_FAILED_ITEM_STATUSES: tuple[str, ...] = (
    "failed",
    "error",
    "cancelled",
    "canceled",
)

_POISONED_SESSION_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "empty output",
    "empty-output",
    "no output",
    "no-output",
    "out of room",
    "context window",
    "clear earlier history",
    "start a new thread",
    "start new thread",
    "no rollout found for thread id",
)


_BACKEND_FAILURE_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "too many requests",
    "429",
    "rate limit",
    "rate-limit",
    "forced restart after hard idle timeout",
    "hard idle timeout",
    "service unavailable",
    "gateway timeout",
    "bad gateway",
    "connection reset",
    "connection closed",
    "connection aborted",
    "network error",
    "acp prompt timed out",
    "acp process died",
    # Codex/Copilot CLI subprocess died mid-turn before emitting a verdict
    # (e.g. gpt-5.5 occasionally exits 2: "Process exited with code 2 before
    # turn completion"). Treat as a transient backend failure so the engineer
    # retries in a fresh session (skip reviewer, backoff, re-run) instead of
    # burning a full reviewer round on a no-output turn; the streak threshold
    # still terminates if it keeps dying.
    "before turn completion",
    "cli exited with code",
)
_AUTH_FAILURE_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "unauthorized",
    "authentication failed",
    "oauth refresh failed",
    "token refresh failed",
    "expired token",
    "invalid token",
    "invalid api key",
    "missing credentials",
    "no authentication information found",
)

_RECOVERABLE_RECONNECT_RE = re.compile(r"^reconnecting\.\.\.\s*(\d+)/(\d+)\b")
_DAEMON_STOP_INTERRUPT_RE = re.compile(r"^external interrupt:\s*daemon stop requested\b")
# Distinct from the daemon-stop interrupt above: this fires when the Manager
# (running in the operator-facing API process) decided mid-mission
# that *this one* backlog item should stop right now — the daemon process
# itself keeps running and will move on to the next ready item. See
# ``argus_skill.life.memory.request_running_item_abort`` for the writer side.
_OPERATOR_ABORT_INTERRUPT_RE = re.compile(
    r"^(?:external interrupt:|refused before start:)\s*operator abort requested\b"
)


def _fatal_error_looks_like_poisoned_session(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _POISONED_SESSION_FATAL_ERROR_PATTERNS)


def fatal_error_looks_like_auth_failure(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _AUTH_FAILURE_FATAL_ERROR_PATTERNS)


def fatal_error_looks_like_backend_failure(fatal_error: str | None) -> bool:
    """Return True for Codex/backend transport failures only.

    The match is intentionally restricted to ``RunnerResult.fatal_error``;
    do not call this on model prose, check output, or command stderr.
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    if fatal_error_looks_like_recoverable_reconnect(fatal_error):
        return False
    return any(pattern in low for pattern in _BACKEND_FAILURE_FATAL_ERROR_PATTERNS)


def fatal_error_looks_like_model_configuration(fatal_error: str | None) -> bool:
    """True when the CLI refused the model or could not reach any model.

    Covers the explicit "model X is not available" diagnostic as well as the
    startup refusals that precede it when the provider session itself is
    unusable (no model catalog, policy denial). All of them pause the mission
    for a provider cooldown rather than failing it.
    """
    if not fatal_error:
        return False
    from ..core.runner_errors import is_provider_access_startup_error

    low = str(fatal_error).strip().casefold()
    return (
        ("--model" in low and "not available" in low)
        or "unknown model" in low
        or "unsupported model" in low
        or is_provider_access_startup_error(fatal_error)
    )


def fatal_error_looks_like_recoverable_reconnect(fatal_error: str | None) -> bool:
    """Return True for Codex CLI reconnect progress notices.

    Codex emits messages such as
    ``Reconnecting... 1/100 (stream disconnected before completion: ...)``.
    The CLI can keep recovering after high attempt counts, so Argus must not
    convert the notice into its own backend-failure state.
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    match = _RECOVERABLE_RECONNECT_RE.search(low)
    return bool(match)


def fatal_error_looks_like_provider_turn_cap(fatal_error: str | None) -> bool:
    """True when a call ended at its per-call provider-turn allowance.

    Matches only the runner's own receipt (see ``agent_cli._run_exec``), never
    model prose. This ending is routine housekeeping — the work done so far is
    kept, and the round loop continues the task in a fresh session — so callers
    must route it around the backend-failure accounting.
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return low.startswith("provider turn cap reached")


def fatal_error_looks_like_daemon_stop_request(fatal_error: str | None) -> bool:
    """Return True for intentional daemon shutdown interrupts."""
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return bool(_DAEMON_STOP_INTERRUPT_RE.search(low))


def fatal_error_looks_like_operator_abort_request(fatal_error: str | None) -> bool:
    """Return True when the Manager aborted *this one* mission on the
    operator's behalf (distinct from a full daemon shutdown — the daemon
    process keeps running and continues with the next ready backlog item).
    """
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return bool(_OPERATOR_ABORT_INTERRUPT_RE.search(low))


def _parse_json_event(raw: object) -> dict | None:
    text = str(raw or "").strip()
    if not text or text[0] not in "{[":
        return None
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def _event_has_successful_work_signal(event: dict) -> bool:
    event_type = str(event.get("type") or "").strip()
    if event_type == "item.completed":
        item = event.get("item") or {}
        if not isinstance(item, dict):
            return False
        kind = str(item.get("type") or "").strip()
        status = str(item.get("status") or "").strip().casefold()
        exit_code = item.get("exit_code")
        if kind == "agent_message":
            return bool(str(item.get("text") or "").strip())
        if status in _FAILED_ITEM_STATUSES:
            return False
        if kind == "command_execution":
            return exit_code == 0 or status in _SUCCESS_ITEM_STATUSES
        if kind in {"file_change", "tool_use"}:
            return status in _SUCCESS_ITEM_STATUSES or bool(item.get("changes"))
        return False
    if event_type in {"tool.result", "assistant.message"}:
        data = event.get("data") or {}
        if isinstance(data, dict):
            return bool(str(data.get("content") or data.get("output") or "").strip())
    return False


def _runner_result_has_successful_work_signal(
    result: RunnerResult,
    *,
    engineer_message: str,
) -> bool:
    if normalize_stop_kind(result.stop_kind) is not None:
        return False
    if engineer_message.strip():
        return True
    if fatal_error_looks_like_backend_failure(result.fatal_error):
        return False

    for raw in result.stdout_lines:
        event = _parse_json_event(raw)
        if event is not None and _event_has_successful_work_signal(event):
            return True
    return False


def runner_result_is_backend_failure(result: RunnerResult) -> bool:
    stop_kind = normalize_stop_kind(result.stop_kind)
    if stop_kind is not None:
        return stop_kind in {"backend_unavailable", "transient_error"}
    return fatal_error_looks_like_backend_failure(result.fatal_error)


# Consecutive backend failures with one normalized signature before the round
# loop stops treating them as independent accidents: it then holds the mission
# with exponential backoff (capped at an hour) and an operator-visible event
# instead of failing into a paid replanning cycle. In one 48-hour window, 353
# error/denied outcomes — most of them the same failure repeated — cost $123
# in retries that could never succeed faster than the provider recovered.
BACKEND_FAILURE_SAME_CAUSE_THRESHOLD = 3
BACKEND_FAILURE_BACKOFF_CAP_SECONDS = 3600.0

_SIGNATURE_NUMBER_RE = re.compile(r"\d+")
_SIGNATURE_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")


def backend_failure_signature(fatal_error: str | None, *, exit_code: int = 0) -> str:
    """Normalize one backend failure into a stable comparison key.

    Two failures share a signature when they differ only in numbers, long hex
    identifiers (thread/request ids), or whitespace — e.g. two 429 responses
    with different retry-after seconds, or the same "model X is not available"
    message across attempts.
    """
    text = str(fatal_error or f"exit={exit_code}").strip().casefold()
    text = _SIGNATURE_HEX_RE.sub("#", text)
    text = _SIGNATURE_NUMBER_RE.sub("#", text)
    return _WHITESPACE_SIGNATURE_RE.sub(" ", text)[:300]


_WHITESPACE_SIGNATURE_RE = re.compile(r"\s+")


def backend_failure_hold_backoff_seconds(
    *,
    same_cause_streak: int,
    base_backoff_seconds: float,
) -> float:
    """Exponential backoff for a repeating identical backend failure.

    Starts doubling once the same cause has been seen
    ``BACKEND_FAILURE_SAME_CAUSE_THRESHOLD`` times and is capped at
    ``BACKEND_FAILURE_BACKOFF_CAP_SECONDS`` (hour scale): retrying faster than
    the underlying cause can change only costs money.
    """
    base = max(1.0, float(base_backoff_seconds or 0.0) or 15.0)
    exponent = max(0, int(same_cause_streak) - BACKEND_FAILURE_SAME_CAUSE_THRESHOLD)
    return float(min(base * (2 ** (exponent + 2)), BACKEND_FAILURE_BACKOFF_CAP_SECONDS))


def should_clear_thread_id_after_outcome(
    *,
    status: str,
    fatal_error: str | None,
    stop_kind: str | None = None,
) -> bool:
    """Return True when the carried Codex thread id should be cleared."""
    return (
        str(status).strip().casefold() == "no_progress"
        or _fatal_error_looks_like_poisoned_session(fatal_error)
        or fatal_error_looks_like_backend_failure(fatal_error)
        or normalize_stop_kind(stop_kind) in {"backend_unavailable", "transient_error"}
    )


def backend_failure_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
    streak: int,
    threshold: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    threshold = max(1, int(threshold or 1))
    retry_text = (
        "Retry in a fresh Codex session; do not resume the failed thread. "
        "If this repeats, pause the daemon and reduce concurrent Codex load."
    )
    return ReviewDecision(
        status="continue",
        reason=(
            "Engineer backend failed before a trustworthy completed turn; "
            f"reviewer skipped. backend_failure_streak={streak}/{threshold}; "
            f"error={error_text}"
        ),
        next_action=retry_text,
    )


def provider_turn_cap_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
    wind_down_summary: str,
    streak: int,
    streak_limit: int,
) -> ReviewDecision:
    """The skipped-review record for a call that used its whole turn allowance.

    ``status="continue"`` on purpose: nothing failed. The Engineer's work up to
    the allowance is kept, the checkpoint carries the state forward, and the
    next round runs the same task in a fresh session. ``next_action`` is what
    that fresh session reads first, so it carries the wind-down summary.
    """
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    summary = str(wind_down_summary or "").strip()
    next_action = (
        "Continue the same task in a fresh session; the previous session ended "
        "at its per-call provider-turn allowance, not because anything went "
        "wrong. Read the continuation note (CHECKPOINT.md) first and pick up "
        "the next action recorded there."
    )
    if summary:
        next_action += (
            " The previous session left this summary before pausing:\n" + summary
        )
    return ReviewDecision(
        status="continue",
        reason=(
            "One Engineer call used its whole per-call provider-turn allowance "
            f"({streak}/{streak_limit} in a row); reviewer skipped. The work so "
            "far is kept and the task continues in a fresh session from the "
            f"checkpoint. Runner receipt: {error_text}"
        ),
        next_action=next_action,
    )


def external_pause_review_decision(
    *,
    stop_kind: str,
    fatal_error: str | None,
    exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    if stop_kind == "daemon_shutdown":
        next_action = "Restart the daemon to resume this mission from its checkpoint."
    elif stop_kind == "operator_pause":
        next_action = "Resume this mission when the operator is ready."
    else:
        next_action = (
            "Resume from the persisted checkpoint after the blocking budget or "
            "provider condition has been cleared."
        )
    return ReviewDecision(
        status="blocked",
        reason=(
            f"Backend call paused before a trustworthy completed turn "
            f"(stop_kind={stop_kind}); reviewer skipped. error={error_text}"
        ),
        next_action=next_action,
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind=normalize_stop_kind(stop_kind),
    )


def model_configuration_review_decision(
    *, fatal_error: str | None, exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        reason=(
            "Configured model is unavailable; Engineer and Reviewer were not "
            f"run. error={error_text}"
        ),
        next_action=(
            "The daemon retries this mission after a provider cooldown. If the "
            "model name is wrong rather than the provider being down, select a "
            "model supported by the configured CLI."
        ),
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind="provider_cooldown",
    )


def authentication_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    low = error_text.casefold()
    if "github-copilot" in low or "copilot" in low:
        action = (
            "Re-authenticate GitHub Copilot in the provider CLI. For Pi, run "
            "`pi`, use `/login`, and choose GitHub Copilot."
        )
    elif "codex" in low:
        action = "Run `codex login` to refresh the configured Codex credentials."
    else:
        action = "Re-authenticate the configured model provider in its CLI."
    question = (
        f"Authentication blocked this mission: {error_text}\n"
        f"{action}\n"
        "After authentication succeeds, confirm here to resume the same mission."
    )
    return ReviewDecision(
        status="blocked",
        reason=error_text,
        next_action=action,
        operator_question=question,
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind="permanent_error",
    )


def daemon_stop_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        # An intentional shutdown, recorded structurally so a consumer reading
        # this decision alone can tell it apart from a real failure instead of
        # having to parse ``reason`` prose.
        backend_stop_kind="daemon_shutdown",
        reason=(
            "Engineer interrupted because daemon shutdown was requested; "
            f"no backend retry was attempted. error={error_text}"
        ),
        next_action=(
            "Restart the daemon when ready; the continuous planner will choose "
            "the next concrete task from the persisted project state."
        ),
    )


def operator_abort_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
    engineer_aborted_before_review: bool = False,
) -> ReviewDecision:
    return ReviewDecision(
        status="blocked",
        # Same contract as the daemon-stop sibling: this was an operator's
        # deliberate abort of ONE mission, not a crash and not a daemon
        # shutdown. Keep it structural so the distinction survives being read
        # apart from the round record that also carries ``stop_kind``.
        backend_stop_kind="operator_abort",
        engineer_aborted_before_review=engineer_aborted_before_review,
        reason="The operator requested this mission be aborted.",
        next_action=(
            "This item was intentionally aborted, not a crash — the daemon "
            "process itself keeps running and will continue with the next "
            "ready backlog item. Re-add this objective later if it still "
            "needs doing."
        ),
    )


__all__ = [
    "BACKEND_FAILURE_SAME_CAUSE_THRESHOLD",
    "BACKEND_FAILURE_BACKOFF_CAP_SECONDS",
    "backend_failure_signature",
    "backend_failure_hold_backoff_seconds",
    "fatal_error_looks_like_backend_failure",
    "fatal_error_looks_like_model_configuration",
    "fatal_error_looks_like_provider_turn_cap",
    "fatal_error_looks_like_recoverable_reconnect",
    "fatal_error_looks_like_daemon_stop_request",
    "fatal_error_looks_like_operator_abort_request",
    "runner_result_is_backend_failure",
    "should_clear_thread_id_after_outcome",
    "backend_failure_review_decision",
    "external_pause_review_decision",
    "model_configuration_review_decision",
    "provider_turn_cap_review_decision",
    "daemon_stop_review_decision",
    "operator_abort_review_decision",
]
