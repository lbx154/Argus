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
from dataclasses import dataclass

from ..core.http_status import has_http_status
from ..core.models import ReviewDecision, RunnerResult
from ..core.stop_kinds import normalize_stop_kind, stop_kind_clause

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


# A session that ended without the CLI naming a cause: the process died
# mid-turn (e.g. gpt-5.5 occasionally exits 2: "Process exited with code 2
# before turn completion"), or Argus's own watchdog ended it. Retrying in a
# fresh session may well succeed, so these are counted by the failure streak
# and retried with backoff; the streak threshold still terminates a session
# that keeps dying.
_SESSION_DEATH_FATAL_ERROR_PATTERNS: tuple[str, ...] = (
    "forced restart after hard idle timeout",
    "hard idle timeout",
    "acp prompt timed out",
    "acp process died",
    "before turn completion",
    "cli exited with code",
)

# What broke when the call never had a working model service behind it. Each
# entry names a kind and the text that identifies it in the runner's failure
# record (the CLI's structured error, or its last stderr lines); the HTTP
# statuses below are recognised in their status context only, so a stray
# number in a log line does not count. Read by code alone: the model is never
# asked to label its own failure. Order matters where one line could match
# two kinds: a sign-in refusal or a quota notice names the service's answer,
# so it outranks the transport it arrived over.
_NETWORK_ERRNO = (
    r"E(?:CONNREFUSED|CONNRESET|CONNABORTED|NOTFOUND|AI_AGAIN|TIMEDOUT"
    r"|HOSTUNREACH|NETUNREACH|NETDOWN|PIPE)"
)
_INFRASTRUCTURE_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sign_in", re.compile(
        r"unauthori[sz]ed|authentication failed|not (?:logged|signed) in"
        r"|token (?:has )?expired|expired token|invalid token|invalid api key"
        r"|missing credentials|no authentication information found"
        r"|oauth refresh failed|token refresh failed|use /login|codex login",
        re.IGNORECASE,
    )),
    ("service_quota", re.compile(
        r"quota|payment required|insufficient (?:credit|fund|balance|trial)",
        re.IGNORECASE,
    )),
    ("cli_missing", re.compile(
        r"\bENOENT\b|command not found|no such file or directory"
        r"|not recognized as an internal or external command"
        r"|executable file not found|cannot find module",
        re.IGNORECASE,
    )),
    ("model_catalog", re.compile(
        r"failed to load models|could not retrieve the list of available models",
        re.IGNORECASE,
    )),
    ("service_tls", re.compile(
        r"\b(?:TLS|SSL)\b|certificate|CERT_HAS_EXPIRED|UNABLE_TO_VERIFY_LEAF_SIGNATURE"
        r"|SELF_SIGNED_CERT_IN_CHAIN|DEPTH_ZERO_SELF_SIGNED_CERT|UNABLE_TO_GET_ISSUER_CERT",
        re.IGNORECASE,
    )),
    ("service_unreachable", re.compile(
        rf"\b{_NETWORK_ERRNO}\b|connection (?:refused|reset|closed|aborted)"
        r"|network error|getaddrinfo|socket hang up|fetch failed|stream disconnected"
        r"|temporary failure in name resolution|no route to host"
        r"|could not connect|unable to connect|connect(?:ion)? timed out|request timed out",
        re.IGNORECASE,
    )),
    ("service_proxy", re.compile(
        r"tunneling socket|proxy (?:error|authentication|connection|connect|refused)"
        r"|(?:https?|all)_proxy\b",
        re.IGNORECASE,
    )),
    ("service_error", re.compile(
        r"too many requests|rate[ -]limit|service unavailable|bad gateway"
        r"|gateway timeout|internal server error|misdirected request|overloaded",
        re.IGNORECASE,
    )),
)
_INFRASTRUCTURE_HTTP_STATUSES: dict[str, frozenset[int]] = {
    "sign_in": frozenset({401, 403}),
    "service_quota": frozenset({402}),
    "service_proxy": frozenset({407}),
    "service_error": frozenset({421, 429, 500, 502, 503, 504}),
}
# Exit codes the shell uses when it cannot run the command at all.
_CLI_MISSING_EXIT_CODES = frozenset({126, 127})
_STACK_FRAME_LINE_RE = re.compile(r"^\s*at\s+\S")
# What a CLI puts in front of the line that matters: timestamps, bracketed
# levels, and the "Error:" label itself.
_CAUSE_LINE_PREFIX_RE = re.compile(
    r"^(?:\[[^\]]*\]\s*|\d{4}-\d\d-\d\d[T ][\d:.]+Z?\s*)*"
    r"(?:(?:error|fatal)\s*:\s*)?",
    re.IGNORECASE,
)
_CAUSE_LINE_LIMIT = 200


@dataclass(frozen=True)
class BackendFailureCause:
    """What broke, as read from the runner's failure record.

    ``kind`` names an infrastructure failure — the CLI could not start, could
    not reach the model service, or the service refused — which no fresh
    session can get past until the service or the operator changes something.
    An empty kind means the model service was there and the session still
    failed (a malformed answer, a turn the provider ended for its own
    reasons): retrying in a fresh session may help. ``line`` is the one line
    of the record that names the cause, ready to be read in a sentence.
    """

    kind: str = ""
    line: str = ""

    @property
    def infrastructure(self) -> bool:
        return bool(self.kind)


def _cause_line(line: str) -> str:
    text = _CAUSE_LINE_PREFIX_RE.sub("", line.strip(), count=1).strip() or line.strip()
    return text[:_CAUSE_LINE_LIMIT]


def backend_failure_cause(
    fatal_error: str | None, *, exit_code: int = 0,
) -> BackendFailureCause:
    """Classify one failure record and pick the line that names its cause.

    Reads the runner's ``fatal_error`` only — the CLI's structured error
    followed by its last stderr lines — never model prose. Later lines are
    read first because a CLI writes its reason last; stack-frame lines are
    skipped so a path inside a trace cannot pass for a cause.
    """
    text = str(fatal_error or "").strip()
    if fatal_error_looks_like_recoverable_reconnect(text):
        return BackendFailureCause()
    lines = [line for line in (raw.strip() for raw in text.splitlines()) if line]
    for line in reversed(lines):
        if _STACK_FRAME_LINE_RE.match(line):
            continue
        for kind, pattern in _INFRASTRUCTURE_FAILURE_PATTERNS:
            statuses = _INFRASTRUCTURE_HTTP_STATUSES.get(kind, ())
            if pattern.search(line) or (statuses and has_http_status(line, statuses)):
                return BackendFailureCause(kind, _cause_line(line))
    if int(exit_code or 0) in _CLI_MISSING_EXIT_CODES:
        return BackendFailureCause(
            "cli_missing", _cause_line(lines[0]) if lines else f"exit={exit_code}",
        )
    for line in reversed(lines):
        if line.casefold().startswith(("error:", "fatal:")):
            return BackendFailureCause("", _cause_line(line))
    return BackendFailureCause("", _cause_line(lines[0]) if lines else "")

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
    return backend_failure_cause(fatal_error).kind == "sign_in"


def fatal_error_looks_like_backend_failure(fatal_error: str | None) -> bool:
    """True when the session ended without a usable result through no fault
    of the task: an infrastructure failure or a session death.

    The match is intentionally restricted to ``RunnerResult.fatal_error``;
    do not call this on model prose, check output, or command stderr.
    """
    if not fatal_error:
        return False
    if fatal_error_looks_like_recoverable_reconnect(fatal_error):
        return False
    if backend_failure_cause(fatal_error).infrastructure:
        return True
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _SESSION_DEATH_FATAL_ERROR_PATTERNS)


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

    The key is the line that names the cause (see ``backend_failure_cause``),
    so the other stderr lines a record carries — timestamps, stack frames —
    cannot make one continuing cause look like a series of different ones.
    Two failures share a signature when their cause lines differ only in
    numbers, long hex identifiers (thread/request ids), or whitespace — e.g.
    two 429 responses with different retry-after seconds, or the same
    "model X is not available" message across attempts.
    """
    cause = backend_failure_cause(fatal_error, exit_code=exit_code)
    text = (cause.line or str(fatal_error or "") or f"exit={exit_code}").strip().casefold()
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
        "Try again in a fresh session rather than resuming the failed one. "
        "If this keeps happening, pause Argus and reduce how many model "
        "sessions run at once."
    )
    # The sentence is what a person reads; the runtime's own facts follow the
    # "Technical record:" marker so a consumer can set them aside.
    return ReviewDecision(
        status="continue",
        reason=(
            "The model service dropped the Engineer's session before it "
            "produced a result that could be checked, so this round was not "
            "judged; Argus retries in a fresh session. "
            f"Technical record: consecutive failures={streak}, "
            f"limit={threshold}, error={error_text}"
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
    rotations = (
        f"{streak}/{streak_limit} in a row"
        if streak_limit > 0 else f"{streak} checkpointed session rotations"
    )
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
            "The Engineer's session reached the length limit for a single "
            f"call before finishing ({rotations}), so this round was not "
            "judged; the work so far is kept and the task continues in a "
            "fresh session from its saved progress. "
            f"Technical record: {error_text}"
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
        next_action = "Start Argus again to resume this task from its saved progress."
    elif stop_kind == "operator_pause":
        next_action = "Resume this task when the operator is ready."
    else:
        next_action = (
            "Resume from the saved progress once the budget or model-service "
            "condition that paused the work has cleared."
        )
    why = stop_kind_clause(stop_kind) or "the work was interrupted"
    return ReviewDecision(
        status="blocked",
        reason=(
            f"The work was paused before the Engineer finished this round "
            f"because {why}, so this round was not judged; it resumes from "
            f"the saved progress. Technical record: stop_kind={stop_kind}; "
            f"error={error_text}"
        ),
        next_action=next_action,
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind=normalize_stop_kind(stop_kind),
    )


def execution_host_review_decision(
    *, fatal_error: str | None, exit_code: int,
) -> ReviewDecision:
    """A missing tool host needs repair before an explicit mission retry."""
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        reason=(
            "The tool environment the Engineer needs could not be started, so "
            "this round did not run and was not judged. "
            f"Technical record: error={error_text}"
        ),
        next_action=(
            "Restore the code-mode host executable in the Codex installation, "
            "then explicitly resume this task to retry from its saved progress."
        ),
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind="backend_unavailable",
    )


# How each infrastructure failure is named to a reader; the cause line from
# the record follows the opening ("The model service could not be reached:
# connect ECONNREFUSED 127.0.0.1:18765").
_INFRASTRUCTURE_FAILURE_OPENINGS: dict[str, str] = {
    "service_unreachable": "The model service could not be reached",
    "service_error": "The model service returned an error",
    "service_tls": "The secure connection to the model service could not be established",
    "service_proxy": "The proxy in front of the model service failed",
    "cli_missing": "The model CLI could not be started",
    "service_quota": "The model service reported that its quota is used up",
    "model_catalog": "The model service could not list its models",
    "sign_in": "Argus could not sign in to the model service",
}


def infrastructure_failure_review_decision(
    *, cause: BackendFailureCause, fatal_error: str | None, exit_code: int,
) -> ReviewDecision:
    """The skipped-review record for a call that never had a model service.

    Paused for a provider cooldown, like a model that is temporarily
    unavailable: the daemon resumes the task after its waiting period, and a
    fresh session in the meantime could only fail the same way.
    """
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    opening = _INFRASTRUCTURE_FAILURE_OPENINGS.get(
        cause.kind, _INFRASTRUCTURE_FAILURE_OPENINGS["service_unreachable"],
    )
    named = f"{opening}: {cause.line}" if cause.line else opening
    return ReviewDecision(
        status="blocked",
        reason=(
            f"{named}. The Engineer's session ended before it produced a "
            "result that could be checked, so this round was not judged; "
            "Argus pauses this task and retries it after a short wait. "
            f"Technical record: error={error_text}"
        ),
        next_action=(
            "Argus retries this task after the model service's waiting period. "
            "If the service does not come back on its own, restore it, then "
            "resume the task."
        ),
        backend_unavailable=True,
        backend_fatal_error=error_text,
        backend_exit_code=exit_code,
        backend_stop_kind="provider_cooldown",
    )


def model_configuration_review_decision(
    *, fatal_error: str | None, exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        reason=(
            "The configured model is unavailable, so neither the Engineer nor "
            "the Reviewer could run this round. "
            f"Technical record: error={error_text}"
        ),
        next_action=(
            "Argus retries this task after the model service's waiting period. "
            "If the model name is wrong rather than the service being down, "
            "choose a model the configured CLI supports."
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
            "Argus was stopped by its operator in the middle of this round; the "
            "Engineer's work so far is kept and nothing was retried. "
            f"Technical record: error={error_text}"
        ),
        next_action=(
            "When Argus is started again it continues from the saved project "
            "state and picks the next concrete task."
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
    "BackendFailureCause",
    "backend_failure_cause",
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
    "execution_host_review_decision",
    "infrastructure_failure_review_decision",
    "model_configuration_review_decision",
    "provider_turn_cap_review_decision",
    "daemon_stop_review_decision",
    "operator_abort_review_decision",
]
