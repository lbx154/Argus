"""SupervisedEngineer: round-loop wrapper around an engineer call.

This is the heart of the argus-skill v0.1 integration:

  * Each round, run the engineer with the current task prompt
    (initial task + optional skill block + optional reviewer next_action
    from prior round).
  * Accept an explicit, decisively verified Engineer self-review waiver for a
    bounded task; otherwise call the Reviewer for a structured verdict.
  * If ``done``, stop. If ``continue``, capture ``next_action`` and loop.
    If ``blocked``, stop and surface the reason.

Provenance: the round-loop control flow is adapted from
``ArgusBot/agent_cli/core/engine.py`` (LoopEngine), simplified to the
single-agent case — argus-skill does not have ArgusBot's planner /
explore subagent; the skill block plays a similar "what to do" role for
the engineer in front of you.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, cast

from ..core.claim_synthesis import claim_synthesis_for_review
from ..core.event_catalog import EventType
from ..core.models import (
    LoopOutcome,
    LoopStatus,
    ReviewDecision,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import (
    SecretScrubReport,
    known_secret_values,
    redact_secrets_record,
    redact_secrets_text,
    scrub_recent_text_artifacts,
)
from ..core.stop_kinds import (
    NON_FAILURE_STOP_KINDS,
    normalize_stop_kind,
    pause_status_for_stop_kind,
    stop_kind_from_external_interrupt,
)
from ..reviewer import Reviewer, ReviewerConfig
from ..reviewer.failure_taxonomy import (
    REPAIRABLE_FAILURE_LAYERS,
    resolve_failure_layer,
)
from .background_subagents import (
    emit_subagent_cost_events,
    find_waitable_subagent,
    inspect_wait_target,
    parse_wait_sentinel,
    render_background_subagents_advisory,
    wait_for_subagent_cadence,
)
from .checkpoint import ensure_shared_checkpoint, shared_checkpoint_instructions
from .external_work import (
    inspect_external_work,
    parse_external_wait_sentinel,
    render_external_work_advisory,
    wait_for_external_work_cadence,
)
from .failure_signature import (
    FailureSignature,
    review_failure_signature,
    signature_similarity,
)
from .long_job_policy import find_unmanaged_long_jobs
from .self_review import (
    EngineerCompletionDecision,
    EngineerSkillMaintenanceOutcome,
    engineer_self_approved_review,
    parse_engineer_completion_decision,
)

log = logging.getLogger(__name__)


def _snapshot_wiki_pages(workdir: Path) -> dict[Path, bytes]:
    """Capture the reviewer-protected wiki card bytes under this project."""
    from ..wiki.auto_hooks import discover_wikis

    snapshot: dict[Path, bytes] = {}
    for wiki_root in discover_wikis(workdir):
        pages_root = wiki_root / "pages"
        if not pages_root.exists():
            continue
        for path in sorted(pages_root.rglob("*.md")):
            if path.is_file():
                snapshot[path] = path.read_bytes()
    return snapshot


def _restore_reviewer_wiki_page_edits(
    workdir: Path,
    snapshot: dict[Path, bytes],
) -> list[str]:
    """Revert direct reviewer writes; WikiRouter is the sole card writer."""
    from ..wiki.auto_hooks import discover_wikis

    current: set[Path] = set()
    for wiki_root in discover_wikis(workdir):
        pages_root = wiki_root / "pages"
        if pages_root.exists():
            current.update(path for path in pages_root.rglob("*.md") if path.is_file())
    changed = sorted(
        path
        for path in current | set(snapshot)
        if path not in snapshot
        or path not in current
        or path.read_bytes() != snapshot[path]
    )
    if not changed:
        return []
    for path in changed:
        original = snapshot.get(path)
        if original is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.reviewer-restore-{uuid.uuid4().hex}")
        try:
            tmp.write_bytes(original)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return [str(path.relative_to(workdir)) for path in changed]

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

_EFFECTIVE_PROGRESS_TIMEOUT_MARKER = "effective progress timeout"
# Distinct marker for the in-round auto-compaction amnesia loop (a busy-but-
# unproductive churn, not a silent stall). Kept separate from the timeout
# marker so fatal-error metrics/searches don't conflate the two failure modes,
# while still reusing the same recoverable between-round handling.
_COMPACTION_THRASH_MARKER = "compaction thrash"
_RECOVERABLE_RECONNECT_RE = re.compile(r"^reconnecting\.\.\.\s*(\d+)/(\d+)\b")
_DAEMON_STOP_INTERRUPT_RE = re.compile(r"^external interrupt:\s*daemon stop requested\b")
# Distinct from the daemon-stop interrupt above: this fires when the Manager
# (running in the operator-facing API process) decided mid-mission
# that *this one* backlog item should stop right now — the daemon process
# itself keeps running and will move on to the next ready item. See
# ``argus_skill.tools.mission_control`` for the writer side of this signal.
_OPERATOR_ABORT_INTERRUPT_RE = re.compile(r"^external interrupt:\s*operator abort requested\b")

_EFFECTIVE_PROGRESS_TIMEOUT_ENV = "ARGUS_SKILL_EFFECTIVE_PROGRESS_TIMEOUT_SECONDS"
_EFFECTIVE_PROGRESS_CHECK_INTERVAL_ENV = (
    "ARGUS_SKILL_EFFECTIVE_PROGRESS_CHECK_INTERVAL_SECONDS"
)
_RUNNER_HARD_IDLE_ENV = "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS"
_SHIFT_ROUND_LIMIT_ENV = "ARGUS_SKILL_SHIFT_ROUND_LIMIT"
_THREAD_TOKEN_LIMIT_ENV = "ARGUS_SKILL_THREAD_TOKEN_LIMIT"
_DECISION_PROGRESS_TIMEOUT_ENV = "ARGUS_SKILL_DECISION_PROGRESS_TIMEOUT_SECONDS"
_ROUND_COMPACTION_LIMIT_ENV = "ARGUS_SKILL_ROUND_COMPACTION_LIMIT"
# Toggle for the background-subagent advisory + agent-driven cadence wait. When
# unset/true, each round surfaces in-flight supervised subagents so the engineer
# does not babysit a self-watched run. Set to 0 to disable (e.g. tests).
_BG_SUBAGENT_ADVISORY_ENV = "ARGUS_SKILL_BG_SUBAGENT_ADVISORY"
_DYNAMIC_PLAN_MODE_ENV = "ARGUS_SKILL_DYNAMIC_PLAN_MODE"
_DYNAMIC_PLAN_CONFIRM_ROUNDS_ENV = "ARGUS_SKILL_DYNAMIC_PLAN_CONFIRM_ROUNDS"
_REPEATED_FAILURE_THRESHOLD_ENV = "ARGUS_SKILL_REPEATED_FAILURE_THRESHOLD"
_REPEATED_FAILURE_SIMILARITY_ENV = "ARGUS_SKILL_REPEATED_FAILURE_SIMILARITY"
_COMPACT_CONTINUATION_PROMPTS_ENV = "ARGUS_SKILL_COMPACT_CONTINUATION_PROMPTS"
_CONTINUE_WORK_SENTINEL = "CONTINUE_WORK:"
_CONTINUE_WORK_MAX_CHARS = 500
# Compatibility defaults for the retired resumed-thread policy. Autonomous
# Engineer/Reviewer calls are always fresh, so no token roll is needed.
_DEFAULT_THREAD_TOKEN_LIMIT = 0
_DEFAULT_DECISION_PROGRESS_TIMEOUT_SECONDS = 30 * 60
_EFFECTIVE_PROGRESS_DEFAULT_TIMEOUT_SECONDS = 60 * 60
# A handful of ``compacted`` events within one fresh Engineer turn indicates an
# in-turn re-read/re-emit loop. Keep this emergency detector independent of the
# cross-round policy; every next round is fresh regardless.
_DEFAULT_ROUND_COMPACTION_LIMIT = 3
_EFFECTIVE_PROGRESS_DEFAULT_CHECK_INTERVAL_SECONDS = 30.0
_EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS = 120.0
_RUNNER_DEFAULT_HARD_IDLE_SECONDS = 60 * 60
_CODEX_SESSION_EVENT_IGNORED_PAYLOAD_TYPES = {"token_count"}
# Top-level Codex session event type written when the agent auto-compacts its
# context. A compaction is the *opposite* of progress (it discards context and
# triggers a re-read loop), so it never counts as effective progress and is
# tallied separately to detect the in-round amnesia thrash.
_CODEX_COMPACTION_EVENT_TYPE = "compacted"
_PROJECT_PROGRESS_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}
_PROJECT_PROGRESS_MAX_FILES = 5000
_PROJECT_PROGRESS_SCAN_BUDGET_SECONDS = 0.35

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


def _fatal_error_looks_like_poisoned_session(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _POISONED_SESSION_FATAL_ERROR_PATTERNS)


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
    """True for an explicit CLI diagnostic rejecting the selected model."""
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return (
        ("--model" in low and "not available" in low)
        or "unknown model" in low
        or "unsupported model" in low
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


def fatal_error_looks_like_effective_progress_timeout(fatal_error: str | None) -> bool:
    """Return True when the semantic-progress watchdog stopped a stale turn."""
    if not fatal_error:
        return False
    return _EFFECTIVE_PROGRESS_TIMEOUT_MARKER in str(fatal_error).strip().casefold()


def fatal_error_looks_like_compaction_thrash(fatal_error: str | None) -> bool:
    """Return True when the watchdog stopped an in-round auto-compaction loop."""
    if not fatal_error:
        return False
    return _COMPACTION_THRASH_MARKER in str(fatal_error).strip().casefold()


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


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _review_event_payload(
    review: ReviewDecision,
    *,
    round_index: int,
    round_max: int,
    text: str,
    review_skipped: bool = False,
    review_source: str = "",
) -> dict[str, object]:
    """Adapter — runner adds ``round_max`` / ``text`` / ``review_skipped``
    on top of the canonical reviewer payload. The reviewer JSON schema's
    full field set lives in ``ReviewDecision.to_event_payload``; this
    keeps engineer-runner and mission-engine emit sites consistent."""
    return redact_secrets_record(
        review.to_event_payload(
            round_index=round_index,
            round_max=round_max,
            text=text,
            review_skipped=review_skipped,
            review_source=review_source,
        ),
        known_values=known_secret_values(),
    )


def _normalize_dynamic_plan_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "off"


def _plan_signal_event(
    review: ReviewDecision,
    *,
    mode: str = "shadow",
    streak: int = 1,
    confirm_rounds: int = 2,
) -> dict[str, object] | None:
    report = getattr(review, "planner_report", None)
    mode = _normalize_dynamic_plan_mode(mode)
    if (
        mode == "off"
        or not isinstance(report, dict)
        or report.get("plan_signal") != "reconsider"
    ):
        return None
    reason = str(report.get("plan_signal_reason") or "").strip()
    if not reason:
        return None
    streak = max(1, int(streak))
    confirm_rounds = max(1, int(confirm_rounds))
    evidence_files = report.get("evidence_files")
    return {
        "type": EventType.LIFE_PLAN_SIGNAL,
        "mode": mode,
        "signal": "reconsider",
        "reason": reason,
        "streak": streak,
        "confirm_rounds": confirm_rounds,
        "confirmed": mode == "active" and streak >= confirm_rounds,
        "evidence_files": evidence_files if isinstance(evidence_files, list) else [],
    }


_EARLIEST_BROKEN_STAGE_PATTERNS = (
    re.compile(
        r"(?i)\bearliest[_\s-]*broken[_\s-]*stage\b\s*(?:is|[:=])\s*`?([a-z0-9_-]+)"
    ),
    re.compile(
        r"(?i)\bearliest\s+broken\s+stage\s+is\s+`?([a-z0-9_-]+)"
    ),
    re.compile(
        r"(?i)\b(?:rollback|return|reopen)\b"
        r"(?:\s+[a-z0-9_-]+){0,6}\s+(?:to\s+)?`?"
        r"(research|plan|benchmark|run|analysis|draft|review|submission)\b"
    ),
)


def _upstream_stage_reconciliation_target(
    review: ReviewDecision,
    *,
    workdir: Path,
) -> str:
    """Return an earlier broken stage that must be adjudicated by Manager.

    A Reviewer can discover that a run-stage mission is invalid because a
    benchmark/plan artifact is broken. Continuing another Engineer round under
    the later stage lets that Engineer bypass Manager stage ownership and may
    execute work that the latest review explicitly forbids.  Detect both a
    future structured field and the currently deployed prose form.
    """
    report = getattr(review, "planner_report", None)
    report = report if isinstance(report, dict) else {}
    candidate = str(report.get("earliest_broken_stage") or "").strip().lower()
    text_parts = [
        str(getattr(review, "reason", "") or ""),
        str(getattr(review, "next_action", "") or ""),
        str(report.get("blocker") or ""),
        str(report.get("recommended_next") or ""),
    ]
    if not candidate:
        joined = "\n".join(text_parts)
        for pattern in _EARLIEST_BROKEN_STAGE_PATTERNS:
            match = pattern.search(joined)
            if match:
                candidate = match.group(1).strip().lower().replace("-", "_")
                break
    if not candidate:
        return ""
    try:
        from ..skills.stage_checklists import (
            _active_vertical_checklist_defs,
            current_stage,
        )

        active_stage = current_stage(workdir).strip().lower().replace("-", "_")
        stage_order, _items = _active_vertical_checklist_defs(workdir)
        order = [str(stage).strip().lower().replace("-", "_") for stage in stage_order]
    except Exception:  # noqa: BLE001 - uncertain stage identity must not reroute
        return ""
    if (
        candidate not in order
        or active_stage not in order
        or order.index(candidate) >= order.index(active_stage)
    ):
        return ""

    if not isinstance(getattr(review, "planner_report", None), dict):
        review.planner_report = report
    report["stage_reconciliation_required"] = True
    report["earliest_broken_stage"] = candidate
    report["plan_signal"] = "reconsider"
    if not str(report.get("plan_signal_reason") or "").strip():
        report["plan_signal_reason"] = (
            f"Reviewer identified upstream stage defect: current={active_stage}, "
            f"earliest_broken_stage={candidate}. Manager rollback adjudication "
            "is required before more Engineer work."
        )
    return candidate


def _apply_round_secret_guard(
    *,
    workdir: Path,
    modified_since: float,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[SecretScrubReport, str]:
    report = scrub_recent_text_artifacts(
        workdir,
        modified_since=modified_since,
        known_values=known_secret_values(),
    )
    if not report.changed and not report.errors and not report.truncated:
        return report, ""
    if on_event:
        on_event({
            "type": EventType.ROUND_SECRET_REDACTED,
            "round_index": round_index,
            "round_max": round_max,
            "redacted_paths": list(report.redacted_paths),
            "replacement_count": report.replacement_count,
            "scanned_files": report.scanned_files,
            "scan_errors": list(report.errors),
            "truncated": report.truncated,
            "operator_alert": bool(report.errors or report.truncated),
        })
    if not report.changed and not report.truncated and not report.errors:
        return report, ""
    lines = [
        "SECURITY GUARD (authoritative artifact hygiene):",
    ]
    if report.changed:
        lines.extend((
            f"- Redacted {report.replacement_count} credential occurrence(s) "
            f"from {len(report.redacted_paths)} changed file(s) before review.",
            "- Files: " + ", ".join(report.redacted_paths),
            "- Revalidate any dependent hashes/provenance; this round is not "
            "complete until the scrubbed artifacts are internally consistent.",
        ))
    if report.truncated:
        lines.append(
            "- Coverage incomplete: at least one recently modified text artifact "
            "exceeded the live-scan size limit. Do not certify completion until "
            "the credential exposure risk is checked."
        )
    if report.errors:
        lines.append(
            "- Coverage incomplete: secret scan errors occurred for "
            + "; ".join(report.errors)
            + ". Do not certify completion until those files are checked."
        )
    return report, "\n".join(lines)


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
        or fatal_error_looks_like_effective_progress_timeout(fatal_error)
        or fatal_error_looks_like_compaction_thrash(fatal_error)
        or fatal_error_looks_like_backend_failure(fatal_error)
        or normalize_stop_kind(stop_kind) in {"backend_unavailable", "transient_error"}
    )


def _runner_result_has_successful_work_signal(
    result: RunnerResult,
    *,
    engineer_message: str,
) -> bool:
    if normalize_stop_kind(getattr(result, "stop_kind", None)) is not None:
        return False
    if fatal_error_looks_like_effective_progress_timeout(
        getattr(result, "fatal_error", None)
    ):
        return False
    if fatal_error_looks_like_compaction_thrash(
        getattr(result, "fatal_error", None)
    ):
        return False
    if engineer_message.strip():
        return True
    if fatal_error_looks_like_backend_failure(getattr(result, "fatal_error", None)):
        return False

    for raw in getattr(result, "stdout_lines", []) or []:
        event = _parse_json_event(raw)
        if event is not None and _event_has_successful_work_signal(event):
            return True
    return False


def runner_result_is_backend_failure(result: RunnerResult) -> bool:
    stop_kind = normalize_stop_kind(getattr(result, "stop_kind", None))
    if stop_kind is not None:
        return stop_kind in {"backend_unavailable", "transient_error"}
    return fatal_error_looks_like_backend_failure(getattr(result, "fatal_error", None))


def parse_continue_work_request(message: str | None) -> str | None:
    """Parse an engineer-requested, bounded continuation before review.

    The request must be the final non-empty line of a substantive response.
    This keeps a quoted example or casual mention from changing control flow,
    while letting the engineer preserve its normal evidence and summary.
    """
    if not message:
        return None
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    line = lines[-1]
    if line.startswith("`") and line.endswith("`") and len(line) >= 2:
        line = line[1:-1].strip()
    if not line.upper().startswith(_CONTINUE_WORK_SENTINEL):
        return None
    next_step = line[len(_CONTINUE_WORK_SENTINEL):].strip()
    if not next_step or len(next_step) > _CONTINUE_WORK_MAX_CHARS:
        return None
    return next_step


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


@dataclass
class EngineerConfig:
    model: str
    reasoning_effort: str | None = None
    initial_reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    # Pipeline stages in which the engineer runs with codex's native live
    # web_search enabled (``codex exec --search``). Default: the research stage,
    # so idea discovery / literature grounding does REAL live search instead of
    # cached/recalled results. Empty set → never enable it.
    live_search_stages: frozenset[str] = frozenset({"research"})


def _engineer_live_search(workdir: Any, stages: "frozenset[str]") -> bool:
    """Whether to enable codex ``--search`` for this engineer round.

    True when the project's current pipeline stage is in ``stages`` (default:
    the research stage, where idea discovery happens). ``current_stage`` resolves
    the framework default (``research``) when no ``PIPELINE_STATE`` exists yet, so
    a fresh/bootstrapping project also gets live search during ideation. Any hard
    error resolving the stage fails closed to False — never break the round.
    """
    if not stages:
        return False
    try:
        from ..skills.stage_checklists import current_stage

        return (current_stage(workdir) or "").strip().lower() in stages
    except Exception:  # noqa: BLE001 — stage lookup must never break the round
        return False


_DECISION_PROGRESS_CLASSES = frozenset({"decision", "evidence"})
_NONDECISION_PROGRESS_CLASSES = frozenset({
    "setup_only",
    "artifact_sync_only",
    "none",
})


def _review_progress_class(review: ReviewDecision) -> str:
    value = str(getattr(review, "progress_class", "") or "").strip().lower()
    if value in _DECISION_PROGRESS_CLASSES | _NONDECISION_PROGRESS_CLASSES:
        return value
    report = getattr(review, "planner_report", None)
    return (
        "none"
        if isinstance(report, dict) and report.get("forward_progress") is False
        else "evidence"
    )


def _next_decision_stall_streak(
    review: ReviewDecision,
    current_streak: int,
) -> int:
    if review.status != "continue":
        return 0
    if _review_progress_class(review) in _DECISION_PROGRESS_CLASSES:
        return 0
    return max(0, int(current_streak)) + 1


def _pause_decision_clock(last_progress_at: float, waited_seconds: float) -> float:
    return float(last_progress_at) + max(0.0, float(waited_seconds or 0.0))


def _run_background_wait(
    *,
    workdir: Path,
    task_id: str,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[str, float]:
    if on_event:
        on_event({
            "type": "round.background_wait.started",
            "round_index": round_index,
            "round_max": round_max,
            "task_id": task_id,
            "text": f"yielding to supervised subagent cadence: {task_id}",
        })
    try:
        wait_reason, waited_s = wait_for_subagent_cadence(workdir, task_id)
    except Exception as exc:  # noqa: BLE001 — a wait must never break the loop
        wait_reason, waited_s = f"error:{type(exc).__name__}", 0.0
    if on_event:
        on_event({
            "type": "round.background_wait.completed",
            "round_index": round_index,
            "round_max": round_max,
            "task_id": task_id,
            "text": (
                f"resumed after {waited_s:.0f}s ({wait_reason}) waiting on {task_id}"
            ),
        })
    return wait_reason, waited_s


def _run_external_work_wait(
    *,
    workdir: Path,
    work_id: str,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[str, float]:
    if on_event:
        on_event({
            "type": "round.external_work_wait.started",
            "round_index": round_index,
            "round_max": round_max,
            "work_id": work_id,
            "text": f"yielding to external-work cadence: {work_id}",
        })
    try:
        wait_reason, waited_s = wait_for_external_work_cadence(workdir, work_id)
    except Exception as exc:  # noqa: BLE001 — a wait must never break the loop
        wait_reason, waited_s = f"error:{type(exc).__name__}", 0.0
    if on_event:
        on_event({
            "type": "round.external_work_wait.completed",
            "round_index": round_index,
            "round_max": round_max,
            "work_id": work_id,
            "reason": wait_reason,
            "text": (
                f"resumed after {waited_s:.0f}s ({wait_reason}) waiting on {work_id}"
            ),
        })
    return wait_reason, waited_s


def _review_wait_rejection(
    workdir: Path,
    task_id: str,
) -> tuple[str, str]:
    return inspect_wait_target(workdir, task_id)


@dataclass
class SupervisedConfig:
    """Knobs for the round-loop control."""
    max_rounds: int = 500
    no_progress_threshold: int = 2  # consecutive rounds with no engineer message before bailing
    # Consecutive reviewed rounds classified by the Reviewer as setup-only,
    # artifact-sync-only, or no decision progress. The harness counts the
    # structured verdict; it never infers scientific progress from activity.
    stall_threshold: int = 4
    # Dynamic Plan is observation-only by default. ``active`` lets two
    # consecutive Reviewer-authored reconsider signals end the current mission
    # cleanly so L4 can replace the remaining backlog plan.
    dynamic_plan_mode: str = field(
        default_factory=lambda: _normalize_dynamic_plan_mode(
            os.environ.get(_DYNAMIC_PLAN_MODE_ENV, "off")
        )
    )
    dynamic_plan_confirm_rounds: int = field(
        default_factory=lambda: _env_int(_DYNAMIC_PLAN_CONFIRM_ROUNDS_ENV, 2)
    )
    # A repeated Reviewer blocker means the mission contract is not producing
    # a new diagnostic action. End cleanly so L4 can replace the plan.
    repeated_failure_threshold: int = field(
        default_factory=lambda: _env_int(_REPEATED_FAILURE_THRESHOLD_ENV, 2)
    )
    repeated_failure_similarity: float = field(
        default_factory=lambda: _env_float(_REPEATED_FAILURE_SIMILARITY_ENV, 0.62)
    )
    # Round 1 receives the full task/skill contract. Continuation rounds use
    # Reviewer guidance plus the shared CHECKPOINT.md baton.
    compact_continuation_prompts: bool = field(
        default_factory=lambda: _env_bool(_COMPACT_CONTINUATION_PROMPTS_ENV, True)
    )
    # Safe round-boundary budget since the last Reviewer-classified decision or
    # evidence increment. This never interrupts a live provider call.
    decision_progress_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            _DECISION_PROGRESS_TIMEOUT_ENV,
            _DEFAULT_DECISION_PROGRESS_TIMEOUT_SECONDS,
        )
    )
    # Anti-livelock escalation — distinct from the stall guards above, which fire
    # when the engineer is idle or the Reviewer classifies repeated rounds as
    # nondecision work. A mission that makes evidence progress every round but
    # never passes its gate would otherwise drift to ``max_rounds``.
    # At ``soft_round_limit`` the reviewer is instructed to return ``blocked`` if
    # the binding constraint is an external/unresolvable dependency; at
    # ``hard_escalate_rounds`` the loop force-ends as ``blocked`` so the planner
    # re-plans/decomposes and the operator inbox is re-read on the next mission.
    # The continuous planner makes many SHORT missions, so a single mission this
    # long without finishing is anomalous. 0 disables either guard.
    soft_round_limit: int = 12
    hard_escalate_rounds: int = 24
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    session_id: str | None = None
    # Absolute path to THIS mission's engineer execution log (the per-project
    # ``<life_dir>/events.jsonl``). The reviewer runs in the project work-tree
    # and only sees the engineer's final summary, so it cannot otherwise tell
    # HOW a result was produced (hardcoded answer? skipped step? cheat method?
    # faked metric?). When set, the reviewer prompt gains an execution-log
    # audit section pointing here with grep recipes; empty string (memory
    # backend / tests / unresolvable path) = legacy behaviour, no audit section,
    # byte-for-byte unchanged. The engineer's shell commands land in the
    # ``text`` field of each ``engineer.progress`` event in this file.
    engineer_log_path: str = ""
    # Retained as a compatibility knob for callers that still construct the
    # config explicitly. Autonomous Engineer/Reviewer calls now always start a
    # fresh provider session, so the value is no longer consulted by the loop.
    shift_round_limit: int = field(
        default_factory=lambda: _env_int(_SHIFT_ROUND_LIMIT_ENV, 1)
    )
    # Compatibility-only alongside ``shift_round_limit``; fresh-per-round calls
    # do not carry a thread whose token count needs policing.
    thread_token_limit: int = field(
        default_factory=lambda: _env_int(
            _THREAD_TOKEN_LIMIT_ENV, _DEFAULT_THREAD_TOKEN_LIMIT
        )
    )
    # Ordinary Markdown file edited directly by Engineer and Reviewer. None
    # disables the shared checkpoint for callers that intentionally opt out.
    checkpoint_path: Path | None = None
    # Mission-level canonical packet. Round handoffs are written beside it.
    context_packet_path: str = ""
    # Kill a live Codex subprocess if it keeps emitting heartbeat/token
    # noise but makes no effective progress for a long time. Effective
    # progress means either a non-token Codex session event or a project file
    # change. The default is intentionally long because paper/research turns
    # often spend many minutes reading, planning, or waiting on model-side
    # recovery before the next file write.
    # Set ARGUS_SKILL_EFFECTIVE_PROGRESS_TIMEOUT_SECONDS=0 to disable.
    effective_progress_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            _EFFECTIVE_PROGRESS_TIMEOUT_ENV,
            _EFFECTIVE_PROGRESS_DEFAULT_TIMEOUT_SECONDS,
        )
    )
    effective_progress_check_interval_seconds: float = field(
        default_factory=lambda: _env_float(
            _EFFECTIVE_PROGRESS_CHECK_INTERVAL_ENV,
            _EFFECTIVE_PROGRESS_DEFAULT_CHECK_INTERVAL_SECONDS,
            minimum=1.0,
        )
    )
    runner_hard_idle_seconds: int = field(
        default_factory=lambda: _env_int(
            _RUNNER_HARD_IDLE_ENV,
            _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        )
    )
    # Interrupt a round once its Codex session auto-compacts this many times
    # within the single round (the in-round amnesia-loop signature). Set
    # ARGUS_SKILL_ROUND_COMPACTION_LIMIT=0 to disable.
    round_compaction_limit: int = field(
        default_factory=lambda: _env_int(
            _ROUND_COMPACTION_LIMIT_ENV,
            _DEFAULT_ROUND_COMPACTION_LIMIT,
        )
    )
    # Surface in-flight SUPERVISED subagents (read from
    # ``<workdir>/.argus_subagents``) in the engineer prompt each round so the
    # agent does not burn rounds babysitting a self-watched long job, and can
    # yield to its supervisor cadence via ``WAIT_FOR_SUBAGENT:`` instead of
    # busy-polling. Env override: ARGUS_SKILL_BG_SUBAGENT_ADVISORY (0 disables).
    background_subagent_advisory: bool = field(
        default_factory=lambda: _env_bool(_BG_SUBAGENT_ADVISORY_ENV, True)
    )
    # The Engineer may explicitly waive independent review after decisive
    # self-verification. Missing/malformed waivers fail closed to Reviewer.
    allow_engineer_self_review: bool = False
    # When a self-approved Engineer requests reusable skill maintenance, resume
    # that exact provider thread for one bounded create/update continuation.
    allow_engineer_skill_maintenance: bool = False
    # Optional sequential-learning contract. The Engineer still authors the
    # skill in its resumed session; the harness only ensures the requested
    # create/update continuation is not accidentally omitted.
    required_skill_action: str = ""
    required_skill_name: str = ""
    # Retained only for source compatibility with older callers.
    review_deferral_limit: int = 0


@dataclass(frozen=True)
class _SessionReadResult:
    """Outcome of tailing the new region of one Codex session jsonl."""

    progressed: bool
    compactions: int
    consumed_bytes: int


def _is_project_progress_ignored_dir(parent: Path, name: str) -> bool:
    """Exclude generated trees that can hide real experiment heartbeats.

    Kernel projects commonly use target-specific environments such as
    ``.venv-b200-tilelang`` instead of the exact ``.venv`` name. Walking one of
    those environments can consume the watchdog's bounded file-scan budget
    before it reaches a growing verifier log under ``research/raw``. Detect a
    Python virtual environment by its standard ``pyvenv.cfg`` marker so custom
    names are ignored without relying on an ever-growing name allowlist.
    """
    if name in _PROJECT_PROGRESS_IGNORE_DIRS:
        return True
    try:
        return (parent / name / "pyvenv.cfg").is_file()
    except OSError:
        return False


class _EffectiveProgressWatchdog:
    """Detect live-but-stale Codex turns.

    Codex can keep a subprocess alive by writing token-count heartbeats
    after the last real tool/message event. The lower-level idle watcher
    sees those heartbeats as stdout activity, so this watchdog looks at
    semantic progress instead.
    """

    def __init__(
        self,
        *,
        workdir: Path,
        timeout_seconds: int,
        check_interval_seconds: float,
        on_event: Callable[[dict], None] | None = None,
        run_label: str | None = None,
        compaction_limit: int = 0,
        now: float | None = None,
    ) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.timeout_seconds = max(0, int(timeout_seconds or 0))
        self.check_interval_seconds = max(1.0, float(check_interval_seconds or 1.0))
        self.on_event = on_event
        self.run_label = run_label
        self.compaction_limit = max(0, int(compaction_limit or 0))
        self.started_at = time.time() if now is None else float(now)
        self.last_effective_progress_at = self.started_at
        self._last_check_at = 0.0
        self._interrupt_reason: str | None = None
        self._interrupted_event_sent = False
        self._compaction_thrash_event_sent = False
        self._compaction_count = 0
        self._last_waiting_event_at = 0.0
        self._project_signature = self._latest_project_signature()
        self._session_root = _codex_sessions_root()
        self._session_offsets = self._initial_session_offsets()
        self._relevant_sessions: set[Path] = set()

    def interrupt_reason(self) -> str | None:
        if self.timeout_seconds <= 0:
            return None
        if self._interrupt_reason:
            return self._interrupt_reason
        now = time.monotonic()
        if now - self._last_check_at < self.check_interval_seconds:
            return None
        self._last_check_at = now
        try:
            self._refresh_effective_progress()
        except Exception:  # noqa: BLE001 - watchdog must never crash a runner
            log.debug("effective progress watchdog check failed", exc_info=True)
            return None

        if (
            self.compaction_limit
            and self._compaction_count >= self.compaction_limit
        ):
            self._interrupt_reason = (
                "compaction thrash: codex auto-compaction amnesia loop — "
                f"{self._compaction_count} compactions within one round "
                f"(limit {self.compaction_limit}); stopping this turn so the "
                "next fresh session can continue from CHECKPOINT.md"
            )
            self._emit_compaction_thrash_event()
            return self._interrupt_reason

        idle_seconds = time.time() - self.last_effective_progress_at
        if idle_seconds < self.timeout_seconds:
            self._emit_waiting_event(idle_seconds)
            return None

        self._interrupt_reason = (
            "effective progress timeout: no non-token Codex session events "
            f"or project file changes for {int(idle_seconds)}s "
            f"(limit {self.timeout_seconds}s)"
        )
        self._emit_interrupted_event(idle_seconds)
        return self._interrupt_reason

    def current_interrupt_reason(self) -> str | None:
        return self._interrupt_reason

    def compaction_count(self) -> int:
        """Number of codex auto-compactions observed this round (F5 hedge):
        the loop forces a full STATIC re-send next round when this is > 0."""
        return int(self._compaction_count)

    def _refresh_effective_progress(self) -> None:
        if self._project_changed():
            self._mark_effective_progress()
        if self._session_progressed():
            self._mark_effective_progress()

    def _mark_effective_progress(self) -> None:
        self.last_effective_progress_at = time.time()

    def _emit_interrupted_event(self, idle_seconds: float) -> None:
        if self._interrupted_event_sent or self.on_event is None:
            return
        self._interrupted_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.effective_progress_timeout",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog event failed", exc_info=True)

    def _emit_compaction_thrash_event(self) -> None:
        if self._compaction_thrash_event_sent or self.on_event is None:
            return
        self._compaction_thrash_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.compaction_thrash",
                "run_label": self.run_label,
                "compaction_count": int(self._compaction_count),
                "limit": int(self.compaction_limit),
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("compaction thrash watchdog event failed", exc_info=True)

    def _emit_waiting_event(self, idle_seconds: float) -> None:
        if self.on_event is None:
            return
        if idle_seconds < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS:
            return
        now = time.time()
        if (
            self._last_waiting_event_at
            and now - self._last_waiting_event_at
            < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS
        ):
            return
        self._last_waiting_event_at = now
        try:
            self.on_event({
                "type": "round.watchdog.waiting",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "text": (
                    "engineer turn is still alive but has no non-token Codex "
                    f"session events or project file changes for {int(idle_seconds)}s "
                    f"(limit {self.timeout_seconds}s)"
                ),
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog waiting event failed", exc_info=True)

    def _project_changed(self) -> bool:
        signature = self._latest_project_signature()
        if signature is None:
            return False
        previous = self._project_signature
        self._project_signature = signature
        if previous is None:
            return signature[0] >= self.started_at - 1.0
        return signature != previous and signature[0] >= self.started_at - 1.0

    def _latest_project_signature(self) -> tuple[float, int, str] | None:
        started = time.monotonic()
        newest: tuple[float, int, str] | None = None
        scanned = 0
        try:
            walker = os.walk(self.workdir, topdown=True)
        except OSError:
            return None
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                name for name in dirnames
                if not _is_project_progress_ignored_dir(Path(dirpath), name)
            ]
            for filename in filenames:
                if scanned >= _PROJECT_PROGRESS_MAX_FILES:
                    return newest
                if time.monotonic() - started > _PROJECT_PROGRESS_SCAN_BUDGET_SECONDS:
                    return newest
                path = Path(dirpath) / filename
                scanned += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                candidate = (float(stat.st_mtime), int(stat.st_size), str(path))
                if newest is None or candidate > newest:
                    newest = candidate
        return newest

    def _initial_session_offsets(self) -> dict[Path, int]:
        offsets: dict[Path, int] = {}
        root = self._session_root
        if root is None:
            return offsets
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return offsets
        for path in paths:
            try:
                offsets[path] = path.stat().st_size
            except OSError:
                continue
        return offsets

    def _session_progressed(self) -> bool:
        root = self._session_root
        if root is None:
            return False
        progressed = False
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return False
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            previous_offset = self._session_offsets.get(path)
            if previous_offset is None:
                previous_offset = 0
            if stat.st_size < previous_offset:
                previous_offset = 0
            if stat.st_size == previous_offset:
                self._session_offsets[path] = stat.st_size
                continue
            if not self._session_relevant(path):
                self._session_offsets[path] = stat.st_size
                continue
            result = self._read_effective_session_events(path, previous_offset)
            if result.progressed:
                progressed = True
            if result.compactions:
                self._compaction_count += result.compactions
            # Advance only past fully newline-terminated lines so a partially
            # written trailing line is re-read (and its eventual ``compacted``
            # event is never silently skipped) on the next poll.
            self._session_offsets[path] = previous_offset + result.consumed_bytes
        return progressed

    def _session_relevant(self, path: Path) -> bool:
        if path in self._relevant_sessions:
            return True
        if _session_cwd_matches(path, self.workdir):
            self._relevant_sessions.add(path)
            return True
        return False

    def _read_effective_session_events(
        self, path: Path, offset: int
    ) -> _SessionReadResult:
        try:
            with path.open("rb") as fh:
                fh.seek(max(0, offset))
                data = fh.read()
        except OSError:
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            # No complete line yet; leave the offset untouched.
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        complete = data[: last_newline + 1]
        progressed = False
        compactions = 0
        for raw in complete.splitlines():
            line = raw.decode("utf-8", errors="replace")
            if _is_codex_compaction_line(line):
                compactions += 1
                continue
            if _is_effective_codex_session_line(line):
                progressed = True
        return _SessionReadResult(
            progressed=progressed,
            compactions=compactions,
            consumed_bytes=len(complete),
        )


def _codex_sessions_root() -> Path | None:
    raw_home = os.environ.get("CODEX_HOME")
    root = Path(raw_home).expanduser() if raw_home else Path.home() / ".codex"
    sessions = root / "sessions"
    return sessions if sessions.is_dir() else None


def _session_cwd_matches(path: Path, workdir: Path) -> bool:
    """Return True when a Codex rollout belongs to this project workdir."""
    target = Path(workdir).expanduser().resolve()
    bytes_read = 0
    max_bytes = 256 * 1024
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                bytes_read += len(line.encode("utf-8", errors="ignore"))
                if bytes_read > max_bytes:
                    return False
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if str(event.get("type") or "") != "session_meta":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = event
                raw_cwd = payload.get("cwd")
                if not isinstance(raw_cwd, str) or not raw_cwd.strip():
                    return False
                try:
                    return Path(raw_cwd).expanduser().resolve() == target
                except OSError:
                    return False
    except OSError:
        return False
    return False


def _is_codex_compaction_line(line: str) -> bool:
    """Return True for a top-level Codex auto-compaction session event."""
    text = line.strip()
    if not text:
        return False
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    return str(event.get("type") or "") == _CODEX_COMPACTION_EVENT_TYPE


def _is_effective_codex_session_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    event_type = str(event.get("type") or "")
    if event_type == "token_count":
        return False
    # A compaction is the opposite of progress: it discards context and kicks
    # off a re-read loop. It must never reset the effective-progress timer.
    if event_type == _CODEX_COMPACTION_EVENT_TYPE:
        return False
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type in _CODEX_SESSION_EVENT_IGNORED_PAYLOAD_TYPES:
            return False
    return True


class SupervisedEngineer:
    """Run the Engineer with self-verification or Reviewer-gated retries.

    Stateless across calls. Construct once with backends, call ``run``
    per task.
    """

    def __init__(
        self,
        *,
        engineer_runner: RunnerBackend,
        reviewer: Reviewer,
        engineer_config: EngineerConfig,
        reviewer_config: ReviewerConfig,
    ) -> None:
        self.engineer_runner = engineer_runner
        self.reviewer = reviewer
        self.engineer_config = engineer_config
        self.reviewer_config = reviewer_config

    def run(
        self,
        *,
        objective: str,
        original_objective: str | None = None,
        engineer_prompt_builder: Callable[[str | None, bool], str],
        supervised_config: SupervisedConfig,
        workdir: Path,
        on_event: Callable[[dict], None] | None = None,
        seed_thread_id: str | None = None,
        scope: str = "",
        prepare_review_context: Callable[[], None] | None = None,
        review_completed_hook: Callable[[RoundRecord], None] | None = None,
        continue_adaptor: Callable[[list[RoundRecord]], str] | None = None,
        reviewer_skill_block: str | None = None,
        engineer_skill_maintenance: Callable[
            [EngineerCompletionDecision, str | None, str],
            EngineerSkillMaintenanceOutcome,
        ] | None = None,
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action, include_static)`` is called once
        per round. Round 1 receives the static task/skill contract; continuation
        rounds default to a compact Reviewer delta plus CHECKPOINT.md. Engineer
        and Reviewer both start fresh provider sessions every round; raw model
        threads are never carried across a round or mission boundary.

        Returns ``(status, rounds, final_message, reason, last_thread_id)``.
        """
        if on_event is not None:
            raw_on_event = on_event

            def _redacted_on_event(event: dict) -> None:
                raw_on_event(
                    redact_secrets_record(
                        event,
                        known_values=known_secret_values(),
                    )
                )

            on_event = _redacted_on_event
        rounds: list[RoundRecord] = []
        last_engineer_message = ""
        no_progress_streak = 0
        semantic_stall_streak = 0
        plan_reconsider_streak = 0
        repeated_failure_streak = 0
        last_failure_signature: FailureSignature | None = None
        reviewer_next_action: str | None = None
        last_decision_progress_at = time.monotonic()
        backend_failure_streak = 0
        reviewer_backend_failure_streak = 0
        pending_secret_guard_notes: list[str] = []
        # ``seed_thread_id`` is intentionally ignored: autonomous role calls are
        # one turn per provider session. The checkpoint file is the baton.
        _ = seed_thread_id
        checkpoint_path = ensure_shared_checkpoint(supervised_config.checkpoint_path)

        for round_index in range(1, supervised_config.max_rounds + 1):
            if on_event:
                try:
                    emit_subagent_cost_events(workdir, on_event)
                except Exception:  # noqa: BLE001
                    log.debug("subagent cost scan ignored an error", exc_info=True)
            # Cross-round role context comes from CHECKPOINT.md, not duplicated
            # free-form reviewer prose in the next Engineer prompt.
            include_static = (
                round_index == 1
                or not supervised_config.compact_continuation_prompts
            )
            engineer_prompt = engineer_prompt_builder(
                reviewer_next_action,
                include_static,
            )
            delta_tail: list[str] = []
            checkpoint_block = shared_checkpoint_instructions(
                checkpoint_path,
                role="engineer",
            )
            if checkpoint_block:
                delta_tail.append(checkpoint_block)
            background_advisory = (
                render_background_subagents_advisory(workdir)
                if supervised_config.background_subagent_advisory
                else ""
            )
            if background_advisory:
                delta_tail.append(background_advisory)
            external_work_advisory = render_external_work_advisory(workdir)
            if external_work_advisory:
                delta_tail.append(external_work_advisory)
            if delta_tail:
                engineer_prompt = engineer_prompt + "\n\n" + "\n\n".join(delta_tail)
            if on_event:
                on_event({
                    "type": EventType.ROUND_START,
                    "round_index": round_index,
                        # Kept for readers of the historical event schema.
                        "round": round_index,
                        "round_max": supervised_config.max_rounds,
                        "prompt_mode": "full" if include_static else "compact",
                        "prompt_chars": len(engineer_prompt),
                        "prompt_estimated_tokens": (len(engineer_prompt) + 3) // 4,
                        "text": f"engineer round {round_index} (fresh session)",
                })
            round_started_at = time.time()
            engineer_result, _round_compactions = self._run_engineer(
                prompt=engineer_prompt,
                workdir=workdir,
                run_label=f"engineer-r{round_index}",
                resume_thread_id=None,
                reasoning_effort=(
                    self.engineer_config.initial_reasoning_effort
                    if round_index == 1
                    else self.engineer_config.reasoning_effort
                ),
                supervised_config=supervised_config,
                on_event=on_event,
            )
            new_tid = getattr(engineer_result, "thread_id", None)
            fatal_error = getattr(engineer_result, "fatal_error", None)
            safe_fatal_error = redact_secrets_text(
                str(fatal_error or ""),
                known_values=known_secret_values(),
            ) or None
            stop_kind = normalize_stop_kind(
                getattr(engineer_result, "stop_kind", None)
            ) or stop_kind_from_external_interrupt(fatal_error)
            round_thread_id = new_tid
            raw_engineer_message = engineer_result.last_agent_message or ""
            engineer_message = redact_secrets_text(
                raw_engineer_message,
                known_values=known_secret_values(),
            )
            if supervised_config.context_packet_path:
                try:
                    from ..life.context_packet import record_engineer_handoff

                    record_engineer_handoff(
                        mission_context_path=supervised_config.context_packet_path,
                        round_index=round_index,
                        engineer_summary=engineer_message,
                        checkpoint_path=checkpoint_path,
                        thread_id=str(round_thread_id or ""),
                    )
                except Exception:  # noqa: BLE001 - handoff persistence is fail-soft
                    log.exception("failed to persist Engineer context packet")
            _secret_report, secret_guard_reviewer_note = _apply_round_secret_guard(
                workdir=workdir,
                modified_since=round_started_at,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                on_event=on_event,
            )
            if (
                secret_guard_reviewer_note
                and secret_guard_reviewer_note not in pending_secret_guard_notes
            ):
                pending_secret_guard_notes.append(secret_guard_reviewer_note)
                del pending_secret_guard_notes[:-8]
            last_engineer_message = engineer_message or last_engineer_message
            unmanaged_long_jobs: list[dict[str, Any]] = []
            if supervised_config.engineer_log_path:
                unmanaged_long_jobs = find_unmanaged_long_jobs(
                    supervised_config.engineer_log_path,
                    call_id=(
                        str(getattr(engineer_result, "call_id", "") or "")
                        if bool(
                            getattr(engineer_result, "call_id_log_correlated", False)
                        )
                        else ""
                    ),
                    since=round_started_at,
                )
            unmanaged_long_job_note = ""
            if unmanaged_long_jobs:
                examples = "\n".join(
                    f"- {row['classification']}: {row['command'][:500]}"
                    for row in unmanaged_long_jobs[:5]
                )
                unmanaged_long_job_note = (
                    "ARGUS LONG-JOB OWNERSHIP WARNING: this Engineer turn launched "
                    "or busy-waited on a long job outside the durable subagent/job "
                    "owner. Preserve any valid terminal artifacts, but classify the "
                    "workflow defect as failure_layer=orchestration and require all "
                    "future long/GPU launches through `python -m "
                    "argus_skill.tools.subagent submit`. Do not interpret owner loss "
                    "or missing finalization as scientific evidence.\n" + examples
                )
                if on_event:
                    on_event({
                        "type": "round.unmanaged_long_job",
                        "round_index": round_index,
                        "count": len(unmanaged_long_jobs),
                        "operator_alert": True,
                        "findings": unmanaged_long_jobs[:5],
                        "text": unmanaged_long_job_note[:2000],
                    })

            # Phase-2 instrumentation: emit ``round.main.completed`` so the
            # supervisor's _CostTrackingSink can fold engineer-side token
            # counts into the iteration budget. Without this event the
            # cost sink only ever sees the reviewer half (and silently
            # under-charges) — leading to ``cost_usd=$0`` in the journal
            # when reviewer tokens were also missing pre-fix.
            if on_event:
                on_event({
                    "type": EventType.ROUND_MAIN_COMPLETED,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": round_thread_id,
                    "exit_code": getattr(engineer_result, "exit_code", 0),
                    "fatal_error": safe_fatal_error,
                    "stop_kind": stop_kind,
                    "last_message": engineer_message,
                    "input_tokens": int(getattr(engineer_result, "input_tokens", 0) or 0),
                    "cached_input_tokens": int(
                        getattr(engineer_result, "cached_input_tokens", 0) or 0
                    ),
                    "output_tokens": int(getattr(engineer_result, "output_tokens", 0) or 0),
                    "reasoning_output_tokens": int(
                        getattr(engineer_result, "reasoning_output_tokens", 0) or 0
                    ),
                    "premium_requests": float(
                        getattr(engineer_result, "premium_requests", 0.0) or 0.0
                    ),
                    "usage_scope": "delta",
                })

            if (
                stop_kind == "daemon_shutdown"
                or fatal_error_looks_like_daemon_stop_request(fatal_error)
            ):
                review = daemon_stop_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text="review: skipped (daemon stop requested)",
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                    stop_kind="daemon_shutdown",
                ))
                return (
                    "paused_daemon_shutdown",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    None,
                )

            if (
                stop_kind == "operator_abort"
                or fatal_error_looks_like_operator_abort_request(fatal_error)
            ):
                review = operator_abort_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text="review: skipped (operator abort requested)",
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                    stop_kind="operator_abort",
                ))
                return (
                    "aborted",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    None,
                )

            if fatal_error_looks_like_model_configuration(fatal_error):
                review = model_configuration_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                )
                if on_event:
                    on_event({
                        "type": "round.model_configuration_error",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "agent_layer": "engineer",
                        "model": self.engineer_config.model,
                        "error": fatal_error,
                        "operator_alert": True,
                        "text": review.reason,
                    })
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text="review: skipped (model unavailable)",
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                ))
                return (
                    "blocked",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    None,
                )

            pause_status = pause_status_for_stop_kind(stop_kind)
            if stop_kind in NON_FAILURE_STOP_KINDS and pause_status:
                review = external_pause_review_decision(
                    stop_kind=stop_kind,
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text=f"review: skipped ({stop_kind})",
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                    stop_kind=stop_kind,
                ))
                return (
                    pause_status,
                    rounds,
                    last_engineer_message,
                    review.reason,
                    round_thread_id,
                )

            if stop_kind == "permanent_error":
                review = backend_failure_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                    streak=1,
                    threshold=1,
                )
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                    stop_kind=stop_kind,
                ))
                return (
                    "error",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    None,
                )

            if runner_result_is_backend_failure(engineer_result):
                backend_failure_streak += 1
                no_progress_streak = 0
                review = backend_failure_review_decision(
                    fatal_error=fatal_error,
                    exit_code=getattr(engineer_result, "exit_code", 0),
                    streak=backend_failure_streak,
                    threshold=supervised_config.backend_failure_threshold,
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text=(
                            "review: skipped (backend failure) — "
                            f"{review.reason}"
                        ),
                        review_skipped=True,
                    ))
                rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                    stop_kind=stop_kind,
                ))
                threshold = max(1, int(supervised_config.backend_failure_threshold or 1))
                if backend_failure_streak >= threshold or round_index >= supervised_config.max_rounds:
                    return (
                        "error",
                        rounds,
                        last_engineer_message,
                        review.reason,
                        None,
                    )
                backoff_seconds = max(
                    0.0, float(supervised_config.backend_failure_backoff_seconds or 0.0)
                )
                if backoff_seconds:
                    if on_event:
                        on_event({
                            "type": "round.backend_failure.backoff",
                            "round_index": round_index,
                            "round_max": supervised_config.max_rounds,
                            "seconds": backoff_seconds,
                            "text": (
                                "backend failure; retrying in a fresh Codex session "
                                f"after {backoff_seconds:.1f}s"
                            ),
                        })
                    time.sleep(backoff_seconds)
                continue

            # Agent-driven cadence yield: if the engineer's entire action this
            # round was a ``WAIT_FOR_SUBAGENT: <id>`` request naming a currently
            # self-watched in-flight subagent, skip the expensive checks+reviewer
            # round and sleep on that subagent's supervisor cadence (waking early
            # if it reaches terminal). This is how the loop honours the advisory's
            # "do not re-poll a self-watched run every round" guidance — the agent
            # explicitly chose to wait, the harness does not decide it. A sentinel
            # naming an unknown / not-self-watched job is ignored (falls through to
            # a normal reviewed round) so a stale request can never hang the loop.
            if supervised_config.background_subagent_advisory:
                wait_task_id = parse_wait_sentinel(raw_engineer_message)
                if wait_task_id and find_waitable_subagent(workdir, wait_task_id) is not None:
                    _, waited_s = _run_background_wait(
                        workdir=workdir,
                        task_id=wait_task_id,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        on_event=on_event,
                    )
                    last_decision_progress_at = _pause_decision_clock(
                        last_decision_progress_at,
                        waited_s,
                    )
                    # A deliberate yield is neither progress nor a stall. Preserve
                    # the pre-wait streak and re-assess fresh next round.
                    continue

            external_work_id = parse_external_wait_sentinel(raw_engineer_message)
            external_work = (
                inspect_external_work(workdir, external_work_id)
                if external_work_id
                else None
            )
            if external_work is not None and external_work.waitable:
                _, waited_s = _run_external_work_wait(
                    workdir=workdir,
                    work_id=external_work.work_id,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    on_event=on_event,
                )
                last_decision_progress_at = _pause_decision_clock(
                    last_decision_progress_at,
                    waited_s,
                )
                continue

            backend_failure_streak = 0
            if not _runner_result_has_successful_work_signal(
                engineer_result, engineer_message=engineer_message
            ):
                no_progress_streak += 1
            else:
                no_progress_streak = 0

            completion_decision = parse_engineer_completion_decision(
                engineer_message
            )
            if (
                completion_decision is not None
                and completion_decision.skill_action == "none"
                and supervised_config.required_skill_action in {"create", "update"}
            ):
                completion_decision = replace(
                    completion_decision,
                    skill_action=supervised_config.required_skill_action,
                    skill_name=supervised_config.required_skill_name,
                    skill_reason=(
                        "Required sequential-learning update from this verified task"
                    ),
                )
            maintenance = EngineerSkillMaintenanceOutcome()
            if (
                completion_decision is not None
                and completion_decision.skill_action != "none"
                and engineer_skill_maintenance is not None
            ):
                try:
                    maintenance = engineer_skill_maintenance(
                        completion_decision,
                        round_thread_id,
                        engineer_message,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("Engineer same-session skill maintenance failed")
                    maintenance = EngineerSkillMaintenanceOutcome(
                        attempted=True,
                        success=False,
                        summary=f"failed: {type(exc).__name__}: {exc}",
                        thread_id=round_thread_id,
                    )
            if (
                completion_decision is not None
                and completion_decision.requests_review_skip
                and supervised_config.allow_engineer_self_review
            ):
                # Engineer owns this judgment. The harness parses the explicit
                # decision but does not second-guess it with extra gates.
                review = engineer_self_approved_review(
                    completion_decision,
                    maintenance_summary=maintenance.summary,
                )
                pending_secret_guard_notes.clear()
                if on_event:
                    on_event({
                        "type": EventType.ENGINEER_SELF_REVIEW_ACCEPTED,
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "reason": completion_decision.reason,
                        "verification": completion_decision.verification,
                        "skill_action": completion_decision.skill_action,
                        "skill_maintenance_attempted": maintenance.attempted,
                        "skill_maintenance_success": maintenance.success,
                        "text": (
                            "Engineer self-verification accepted; "
                            "independent Reviewer waived"
                        ),
                    })
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text=(
                            "review: skipped (Engineer self-verification) — "
                            + review.reason
                        ),
                        review_skipped=True,
                        review_source="engineer_self_review",
                    ))
                record = RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                )
                rounds.append(record)
                if review_completed_hook is not None:
                    try:
                        review_completed_hook(record)
                    except Exception:  # noqa: BLE001
                        log.warning(
                            "self-review completion hook failed",
                            exc_info=True,
                        )
                return (
                    "done",
                    rounds,
                    last_engineer_message,
                    review.reason,
                    maintenance.thread_id or round_thread_id,
                )

            if prepare_review_context is not None:
                try:
                    prepare_review_context()
                except Exception:  # noqa: BLE001 — context prep must not hide a verdict
                    log.warning("review context preparation failed", exc_info=True)
            if on_event:
                on_event({
                    "type": EventType.ROUND_REVIEW_STARTED,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": supervised_config.session_id,
                })
            # Anti-livelock escalation hint: past the soft round limit, tell the
            # reviewer to escalate an unresolvable EXTERNAL blocker to `blocked`
            # (which ends the mission) rather than looping `continue` forever.
            escalate_hint = ""
            if (
                supervised_config.soft_round_limit
                and round_index >= supervised_config.soft_round_limit
            ):
                escalate_hint = (
                    f"This mission has now run {round_index} rounds without "
                    "reaching `done`. If the binding constraint is an EXTERNAL "
                    "blocker the engineer cannot resolve by itself — infrastructure, "
                    "GPU quota / preemption, missing credentials, or a host that "
                    "stays unreachable after retries — return status=`blocked` with "
                    "a precise operator ask INSTEAD of `continue`. Do not keep "
                    "looping on an unresolvable external dependency.\n"
                    "This also applies to an INTERNAL blocker: if the last 2+ "
                    "rounds have independently re-derived the SAME root-cause "
                    "finding that a frozen upstream artifact/contract (e.g. plan, "
                    "run contract, curriculum, checklist) is defective and the fix "
                    "requires a Manager-owned stage rollback or edit this mission's "
                    "own scope forbids performing, do not keep re-verifying that "
                    "same finding. Return status=`blocked` with `reason` naming the "
                    "repeated finding and the exact stage/artifact that needs "
                    "Manager-owned repair, so the mission ends now and control "
                    "returns to the Planner/Manager instead of waiting for the "
                    "hard round cap."
                )
                if on_event and round_index == supervised_config.soft_round_limit:
                    on_event({
                        "type": EventType.ROUND_ESCALATED,
                        "round_index": round_index,
                        "soft_round_limit": supervised_config.soft_round_limit,
                        "hard_escalate_rounds": supervised_config.hard_escalate_rounds,
                        "text": (
                            f"round {round_index} reached soft limit "
                            f"{supervised_config.soft_round_limit}: reviewer asked to "
                            "escalate external blockers to `blocked`"
                        ),
                    })
            # Evaluate the reviewer, retrying ONLY the reviewer on an infra flake.
            # The engineer's output for THIS round is already valid and in hand, so
            # a reviewer subprocess crash / 429 / missing-output-schema must retry
            # the (cheap) reviewer leg — NOT discard the round and re-run the
            # (xhigh) engineer turn. We leave this inner loop with a real verdict,
            # or by failing loud once the reviewer-backend streak hits threshold.
            reviewer_direct_write_violations = 0
            while True:
                wiki_pages_before_review = _snapshot_wiki_pages(workdir)
                reviewer_background_context = ""
                if supervised_config.background_subagent_advisory:
                    try:
                        reviewer_background_context = (
                            "\n\n".join(
                                block
                                for block in (
                                    render_background_subagents_advisory(workdir),
                                    render_external_work_advisory(workdir),
                                )
                                if block
                            )
                        )
                    except Exception:  # noqa: BLE001 — advisory is non-critical context
                        log.debug(
                            "reviewer subagent advisory refresh failed",
                            exc_info=True,
                        )
                try:
                    review = self.reviewer.evaluate(
                        objective=objective,
                        original_objective=original_objective or objective,
                        round_index=round_index,
                        session_id=supervised_config.session_id,
                        main_summary=(
                            "\n\n".join(
                                part
                                for part in (
                                    engineer_message or "(no message)",
                                    *pending_secret_guard_notes,
                                    unmanaged_long_job_note,
                                )
                                if part
                            )
                        ),
                        main_error=safe_fatal_error,
                        config=replace(
                            self.reviewer_config,
                            working_dir=str(workdir),
                        ),
                        prev_review_summary="",
                        scope=scope,
                        checkpoint_path=str(checkpoint_path or ""),
                        background_context=reviewer_background_context,
                        escalate_hint=escalate_hint,
                        engineer_log_path=supervised_config.engineer_log_path,
                        engineer_call_id=(
                            str(getattr(engineer_result, "call_id", "") or "")
                            if bool(
                                getattr(
                                    engineer_result,
                                    "call_id_log_correlated",
                                    False,
                                )
                            )
                            else ""
                        ),
                        preselected_skill_block=reviewer_skill_block,
                        resume_thread_id=None,
                        prior_static_fingerprint="",
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"reviewer raised {type(exc).__name__}: {exc}"
                    log.exception("reviewer raised during supervised round")
                    review = ReviewDecision(
                        status="blocked",
                        reason=msg,
                        next_action="Resolve the reviewer runner failure before retrying.",
                        round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                        completion_summary_markdown="",
                        failure_cause="environmental",
                        backend_unavailable=True,
                        backend_stop_kind="backend_unavailable",
                    )
                direct_wiki_edits = _restore_reviewer_wiki_page_edits(
                    workdir,
                    wiki_pages_before_review,
                )
                if direct_wiki_edits:
                    reviewer_direct_write_violations += 1
                    paths = ", ".join(direct_wiki_edits[:8])
                    violation = (
                        "Reviewer directly modified protected wiki card files "
                        f"({paths}). Those writes were atomically reverted. "
                        "Return the intended changes only through structured "
                        "`wiki_ops`; WikiRouter is the sole writer for wiki/pages."
                    )
                    if violation not in pending_secret_guard_notes:
                        pending_secret_guard_notes.append(violation)
                    if on_event:
                        on_event({
                            "type": EventType.WIKI_REVIEWER_DIRECT_WRITE_REVERTED,
                            "round_index": round_index,
                            "paths": direct_wiki_edits,
                            "operator_alert": True,
                            "text": violation,
                        })
                    if reviewer_direct_write_violations < 3:
                        continue
                    review = ReviewDecision(
                        status="blocked",
                        reason=violation,
                        next_action=(
                            "Retry this Reviewer turn and express the same judgment "
                            "through schema-valid wiki_ops."
                        ),
                        round_summary_markdown=(
                            "# Reviewer write-boundary violation\n\n"
                            f"- Reverted: {paths}\n"
                        ),
                        completion_summary_markdown="",
                        failure_cause="environmental",
                        backend_unavailable=True,
                        backend_stop_kind="transient_error",
                        backend_fatal_error=violation,
                    )
                reviewer_fatal_error = str(
                    getattr(review, "backend_fatal_error", "") or ""
                )
                reviewer_exit_code = int(
                    getattr(review, "backend_exit_code", 0) or 0
                )
                reviewer_stop_kind = normalize_stop_kind(
                    getattr(review, "backend_stop_kind", None)
                ) or stop_kind_from_external_interrupt(reviewer_fatal_error)
                reviewer_pause_status = pause_status_for_stop_kind(
                    reviewer_stop_kind
                )
                if (
                    getattr(review, "backend_unavailable", False)
                    and reviewer_stop_kind in NON_FAILURE_STOP_KINDS
                    and reviewer_pause_status
                ):
                    if on_event:
                        on_event(_review_event_payload(
                            review,
                            round_index=round_index,
                            round_max=supervised_config.max_rounds,
                            text=f"review: skipped ({reviewer_stop_kind})",
                            review_skipped=True,
                        ))
                    rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=review,
                        fatal_error=reviewer_fatal_error,
                        stop_kind=reviewer_stop_kind,
                    ))
                    return (
                        reviewer_pause_status,
                        rounds,
                        last_engineer_message,
                        review.reason,
                        None,
                    )
                if (
                    getattr(review, "backend_unavailable", False)
                    and reviewer_stop_kind == "permanent_error"
                ):
                    rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=review,
                        fatal_error=reviewer_fatal_error,
                        stop_kind=reviewer_stop_kind,
                    ))
                    return (
                        "error",
                        rounds,
                        last_engineer_message,
                        review.reason,
                        None,
                    )
                if (
                    getattr(review, "backend_unavailable", False)
                    and (
                        reviewer_stop_kind == "operator_abort"
                        or fatal_error_looks_like_operator_abort_request(
                            reviewer_fatal_error
                        )
                    )
                ):
                    interrupted_review = operator_abort_review_decision(
                        fatal_error=reviewer_fatal_error,
                        exit_code=reviewer_exit_code,
                    )
                    interrupted_review = replace(
                        interrupted_review,
                        input_tokens=int(getattr(review, "input_tokens", 0) or 0),
                        cached_input_tokens=int(
                            getattr(review, "cached_input_tokens", 0) or 0
                        ),
                        output_tokens=int(getattr(review, "output_tokens", 0) or 0),
                        reasoning_output_tokens=int(
                            getattr(review, "reasoning_output_tokens", 0) or 0
                        ),
                        premium_requests=float(
                            getattr(review, "premium_requests", 0.0) or 0.0
                        ),
                    )
                    if on_event:
                        on_event(_review_event_payload(
                            interrupted_review,
                            round_index=round_index,
                            round_max=supervised_config.max_rounds,
                            text="review: skipped (operator abort requested)",
                            review_skipped=True,
                        ))
                    rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=interrupted_review,
                        fatal_error=reviewer_fatal_error,
                        stop_kind="operator_abort",
                    ))
                    return (
                        "aborted",
                        rounds,
                        last_engineer_message,
                        interrupted_review.reason,
                        None,
                    )
                if (
                    getattr(review, "backend_unavailable", False)
                    and (
                        reviewer_stop_kind == "daemon_shutdown"
                        or fatal_error_looks_like_daemon_stop_request(
                            reviewer_fatal_error
                        )
                    )
                ):
                    interrupted_review = daemon_stop_review_decision(
                        fatal_error=reviewer_fatal_error,
                        exit_code=reviewer_exit_code,
                    )
                    if on_event:
                        on_event(_review_event_payload(
                            interrupted_review,
                            round_index=round_index,
                            round_max=supervised_config.max_rounds,
                            text="review: skipped (daemon stop requested)",
                            review_skipped=True,
                        ))
                    rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=interrupted_review,
                        fatal_error=reviewer_fatal_error,
                        stop_kind="daemon_shutdown",
                    ))
                    return (
                        "paused_daemon_shutdown",
                        rounds,
                        last_engineer_message,
                        interrupted_review.reason,
                        None,
                    )
                # Reviewer backend death (codex subprocess died / output-schema
                # missing / runner raised) renders NO verdict. It must NEVER be
                # laundered into a silent "continue": on 2026-06-25 a stale
                # import-time schema path made every reviewer round exit 1, and the
                # loop ran the sole completion gate BLIND for ~1.5h. Route it through
                # the SAME transient-backoff + escalate-to-error machinery the
                # engineer backend-failure path uses. ``backend_unavailable`` is an
                # explicit infra-death marker — distinct from a genuine
                # ``status="blocked"`` verdict (e.g. "blocked on GPU quota"), which
                # is a real model judgment and is handled normally by ``_classify``.
                if not getattr(review, "backend_unavailable", False):
                    break
                reviewer_backend_failure_streak += 1
                rb_threshold = max(
                    1, int(supervised_config.backend_failure_threshold or 1)
                )
                if on_event:
                    on_event(_review_event_payload(
                        review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text=(
                            "review: skipped (reviewer backend unavailable) — "
                            f"{review.reason}"
                        ),
                        review_skipped=True,
                    ))
                    on_event({
                        "type": EventType.ROUND_REVIEWER_BACKEND_FAILURE,
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "streak": reviewer_backend_failure_streak,
                        "threshold": rb_threshold,
                        "operator_alert": True,
                        "text": (
                            "reviewer backend unavailable "
                            f"{reviewer_backend_failure_streak}/{rb_threshold}: no "
                            "verdict rendered — NOT continuing blind. "
                            + review.reason
                        ),
                    })
                if (
                    reviewer_backend_failure_streak >= rb_threshold
                    or round_index >= supervised_config.max_rounds
                ):
                    # Failing loud: record this round (with the in-hand engineer
                    # output) and stop — do not run the completion gate blind.
                    rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=review,
                        fatal_error=engineer_result.fatal_error,
                    ))
                    return (
                        "error",
                        rounds,
                        last_engineer_message,
                        (
                            "Reviewer backend unavailable for "
                            f"{reviewer_backend_failure_streak} consecutive "
                            "attempt(s); failing loud rather than running the "
                            "completion gate without a real review. "
                            + review.reason
                        ),
                        None,
                    )
                backoff_seconds = max(
                    0.0,
                    float(supervised_config.backend_failure_backoff_seconds or 0.0),
                )
                if backoff_seconds:
                    if on_event:
                        on_event({
                            "type": "round.reviewer_backend_failure.backoff",
                            "round_index": round_index,
                            "round_max": supervised_config.max_rounds,
                            "seconds": backoff_seconds,
                            "text": (
                                "reviewer backend unavailable; retrying after "
                                f"{backoff_seconds:.1f}s"
                            ),
                        })
                    time.sleep(backoff_seconds)
                # Retry ONLY the reviewer against the SAME engineer output — do
                # not fall through to a fresh (xhigh) engineer turn.
                continue
            # A real reviewer verdict arrived — reset the reviewer-backend streak.
            reviewer_backend_failure_streak = 0
            pending_secret_guard_notes.clear()
            progress_class = _review_progress_class(review)
            next_semantic_stall_streak = _next_decision_stall_streak(
                review,
                semantic_stall_streak,
            )
            planner_report = getattr(review, "planner_report", None)
            upstream_stage_target = _upstream_stage_reconciliation_target(
                review,
                workdir=workdir,
            )
            planner_report = getattr(review, "planner_report", None)
            failure_signature = review_failure_signature(review)
            failure_similarity = 0.0
            if failure_signature is None:
                repeated_failure_streak = 0
                last_failure_signature = None
            elif last_failure_signature is None:
                repeated_failure_streak = 1
                last_failure_signature = failure_signature
            else:
                failure_similarity = signature_similarity(
                    last_failure_signature,
                    failure_signature,
                )
                if failure_similarity >= float(
                    supervised_config.repeated_failure_similarity
                ):
                    repeated_failure_streak += 1
                else:
                    repeated_failure_streak = 1
                last_failure_signature = failure_signature
            repeated_failure_replan = bool(
                failure_signature is not None
                and supervised_config.repeated_failure_threshold > 0
                and repeated_failure_streak
                >= supervised_config.repeated_failure_threshold
            )
            repeated_failure_reason = ""
            if repeated_failure_replan and not upstream_stage_target:
                repeated_failure_reason = (
                    "The same reviewed failure signature repeated "
                    f"{repeated_failure_streak} times (similarity "
                    f"{failure_similarity:.2f}). Stop rerunning the unchanged "
                    "mission; replan into the cheapest targeted diagnostic or "
                    "scoped repair, then return to the decisive gate."
                )
                report = dict(planner_report or {})
                report["plan_signal"] = "reconsider"
                report["plan_signal_reason"] = repeated_failure_reason
                report["forward_progress"] = False
                report["recommended_next"] = repeated_failure_reason
                review.planner_report = report
                planner_report = report
            reconsidered = (
                review.status == "continue"
                and isinstance(planner_report, dict)
                and planner_report.get("plan_signal") == "reconsider"
                and bool(str(planner_report.get("plan_signal_reason") or "").strip())
            )
            plan_reconsider_streak = (
                plan_reconsider_streak + 1 if reconsidered else 0
            )
            dynamic_plan_mode = _normalize_dynamic_plan_mode(
                supervised_config.dynamic_plan_mode
            )
            confirm_rounds = max(
                1, int(supervised_config.dynamic_plan_confirm_rounds or 1)
            )
            plan_signal_event = _plan_signal_event(
                review,
                mode=dynamic_plan_mode,
                streak=max(1, plan_reconsider_streak),
                confirm_rounds=confirm_rounds,
            )
            plan_reconsider_confirmed = bool(
                plan_signal_event is not None
                and plan_signal_event.get("confirmed") is True
            )
            now_monotonic = time.monotonic()
            next_decision_progress_at = (
                now_monotonic
                if progress_class in _DECISION_PROGRESS_CLASSES
                else last_decision_progress_at
            )
            if on_event:
                on_event(_review_event_payload(
                    review,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    text=f"review: {review.status} — {review.reason}",
                ))
                if plan_signal_event is not None:
                    plan_signal_event["round_index"] = round_index
                    on_event(plan_signal_event)
            record = RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
            )
            rounds.append(record)
            reviewer_next_action = (
                review.next_action if review.status == "continue" else None
            )
            if review_completed_hook is not None:
                try:
                    review_completed_hook(record)
                except Exception:  # noqa: BLE001 - memory capture never owns verdict
                    log.warning("review completion hook failed", exc_info=True)

            if upstream_stage_target:
                reconciliation_reason = str(
                    planner_report.get("plan_signal_reason")
                    if isinstance(planner_report, dict)
                    else ""
                ).strip()
                if on_event:
                    on_event({
                        "type": EventType.LIFE_PLAN_SIGNAL,
                        "mode": "active",
                        "signal": "stage_reconciliation",
                        "confirmed": True,
                        "target_stage": upstream_stage_target,
                        "reason": reconciliation_reason,
                        "round_index": round_index,
                    })
                return (
                    "replan_requested",
                    rounds,
                    last_engineer_message,
                    reconciliation_reason,
                    None,
                )

            if repeated_failure_replan:
                if on_event:
                    on_event({
                        "type": "round.failure_signature.repeated",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "signature": failure_signature.digest,
                        "streak": repeated_failure_streak,
                        "similarity": round(failure_similarity, 3),
                        "failure_cause": failure_signature.failure_cause,
                        "unsatisfied_items": list(
                            failure_signature.unsatisfied_items
                        ),
                        "operator_alert": True,
                        "text": repeated_failure_reason,
                    })
                return (
                    "replan_requested",
                    rounds,
                    last_engineer_message,
                    repeated_failure_reason,
                    None,
                )

            if plan_reconsider_confirmed:
                return (
                    "replan_requested",
                    rounds,
                    last_engineer_message,
                    str(planner_report.get("plan_signal_reason") or "").strip(),
                    None,
                )

            if getattr(review, "control_action", "") == "wait_for_subagent":
                rejection_code = ""
                rejection_reason = ""
                wait_task_id = str(getattr(review, "control_task_id", "") or "").strip()
                if not supervised_config.background_subagent_advisory:
                    rejection_code = "advisory_disabled"
                    rejection_reason = (
                        "background-subagent advisory is disabled in this mission"
                    )
                elif review.status != "continue":
                    rejection_code = "review_status_not_continue"
                    rejection_reason = (
                        f"review status `{review.status}` cannot request a wait"
                    )
                elif inspect_wait_target(workdir, wait_task_id)[0] == "waitable":
                    _, waited_s = _run_background_wait(
                        workdir=workdir,
                        task_id=wait_task_id,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        on_event=on_event,
                    )
                    last_decision_progress_at = _pause_decision_clock(
                        next_decision_progress_at,
                        waited_s,
                    )
                    continue
                else:
                    rejection_code, rejection_reason = _review_wait_rejection(
                        workdir,
                        wait_task_id,
                    )
                if on_event:
                    on_event({
                        "type": "round.background_wait.rejected",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "task_id": wait_task_id,
                        "reason_code": rejection_code,
                        "reason": rejection_reason,
                        "text": (
                            "reviewed background wait rejected: "
                            f"{rejection_reason}"
                        ),
                    })

            semantic_stall_streak = next_semantic_stall_streak
            last_decision_progress_at = next_decision_progress_at
            decision_idle_seconds = max(
                0.0,
                now_monotonic - last_decision_progress_at,
            )
            if on_event and semantic_stall_streak > 0:
                on_event({
                    "type": EventType.ROUND_STALL,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "progress_class": progress_class,
                    "semantic_stall_streak": semantic_stall_streak,
                    "stall_threshold": supervised_config.stall_threshold,
                    "decision_idle_seconds": round(decision_idle_seconds, 1),
                    "text": (
                        f"no decision progress {semantic_stall_streak}/"
                        f"{supervised_config.stall_threshold} rounds"
                    ),
                })
            terminal_status, reason = self._classify(
                review=review,
                no_progress_streak=no_progress_streak,
                no_progress_threshold=supervised_config.no_progress_threshold,
                semantic_stall_streak=semantic_stall_streak,
                stall_threshold=supervised_config.stall_threshold,
                round_index=round_index,
                max_rounds=supervised_config.max_rounds,
                hard_escalate_rounds=supervised_config.hard_escalate_rounds,
                decision_idle_seconds=decision_idle_seconds,
                decision_timeout_seconds=(
                    supervised_config.decision_progress_timeout_seconds
                ),
            )
            if terminal_status is not None:
                return (
                    terminal_status,
                    rounds,
                    last_engineer_message,
                    reason,
                    None,
                )

            if continue_adaptor is not None:
                try:
                    continue_adaptor(rounds)
                except Exception:  # noqa: BLE001 — adaptation is advisory
                    log.debug("continue adaptor failed", exc_info=True)

        return (
            "max_rounds",
            rounds,
            last_engineer_message,
            f"Hit max_rounds={supervised_config.max_rounds} without reviewer-confirmed completion.",
            None,
        )

    def _run_engineer(
        self,
        *,
        prompt: str,
        workdir: Path,
        run_label: str,
        resume_thread_id: str | None = None,
        reasoning_effort: str | None = None,
        supervised_config: SupervisedConfig | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[RunnerResult, int]:
        effective_progress_provider: Callable[[], str | None] | None = None
        effective_progress_watchdog: _EffectiveProgressWatchdog | None = None
        hard_idle_seconds = 0
        if supervised_config is not None:
            timeout_seconds = int(
                supervised_config.effective_progress_timeout_seconds or 0
            )
            hard_idle_seconds = int(supervised_config.runner_hard_idle_seconds or 0)
            if timeout_seconds > 0:
                effective_progress_watchdog = _EffectiveProgressWatchdog(
                    workdir=workdir,
                    timeout_seconds=timeout_seconds,
                    check_interval_seconds=(
                        supervised_config.effective_progress_check_interval_seconds
                    ),
                    on_event=on_event,
                    run_label=run_label,
                    compaction_limit=supervised_config.round_compaction_limit,
                )
                effective_progress_provider = effective_progress_watchdog.interrupt_reason
        try:
            result = gateway_run_exec(
                self.engineer_runner,
                prompt=prompt,
                options=RunnerOptions(
                    model=self.engineer_config.model,
                    reasoning_effort=(
                        reasoning_effort
                        if reasoning_effort is not None
                        else self.engineer_config.reasoning_effort
                    ),
                    extra_args=self.engineer_config.extra_args,
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    working_dir=str(workdir),
                    live_search=_engineer_live_search(
                        workdir, self.engineer_config.live_search_stages
                    ),
                    external_interrupt_reason_provider=effective_progress_provider,
                    watchdog_hard_idle_seconds=hard_idle_seconds,
                ),
                run_label=run_label,
                resume_thread_id=resume_thread_id,
            )
            # F5: per-round compaction count (0 when no watchdog) — the loop forces
            # a full STATIC re-send next round when this is > 0 (anti-amnesia hedge).
            _compactions = (
                effective_progress_watchdog.compaction_count()
                if effective_progress_watchdog is not None
                else 0
            )
            if (
                effective_progress_watchdog is not None
                and effective_progress_watchdog.current_interrupt_reason()
                and not getattr(result, "fatal_error", None)
            ):
                return replace(
                    result,
                    exit_code=(
                        int(getattr(result, "exit_code", 0) or 0)
                        if int(getattr(result, "exit_code", 0) or 0) != 0
                        else -1
                    ),
                    fatal_error=effective_progress_watchdog.current_interrupt_reason(),
                ), _compactions
            return result, _compactions
        except Exception as exc:  # noqa: BLE001
            msg = f"engineer runner raised {type(exc).__name__}: {exc}"
            log.exception("engineer runner raised during %s", run_label)
            return RunnerResult(
                exit_code=-1,
                fatal_error=msg,
                stderr_lines=[msg],
                stop_kind="backend_unavailable",
            ), 0

    @staticmethod
    def _classify(
        *,
        review: ReviewDecision,
        no_progress_streak: int,
        no_progress_threshold: int,
        semantic_stall_streak: int = 0,
        stall_threshold: int = 0,
        round_index: int,
        max_rounds: int,
        hard_escalate_rounds: int = 0,
        decision_idle_seconds: float = 0.0,
        decision_timeout_seconds: int = 0,
    ) -> tuple[LoopStatus | None, str]:
        if review.status == "done":
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status == "blocked":
            failure_layer = resolve_failure_layer(
                failure_layer=getattr(review, "failure_layer", ""),
                failure_cause=getattr(review, "failure_cause", ""),
            )
            if (
                failure_layer in REPAIRABLE_FAILURE_LAYERS
                and not review.operator_question
            ):
                return (
                    "replan_requested",
                    f"Repairable {failure_layer} failure; scientific state is "
                    "unchanged. Replan to repair the failed layer, validate it, "
                    "and resume the scientific experiment. "
                    + (review.reason or ""),
                )
            claim_synthesis = claim_synthesis_for_review(review)
            if failure_layer == "scientific" and claim_synthesis is not None:
                return (
                    "replan_requested",
                    "Valid scientific result routed to "
                    f"{claim_synthesis['route']} / {claim_synthesis['action']}; "
                    "do not discard the data or hold the campaign. "
                    + (review.reason or ""),
                )
            if review.failure_cause == "environmental" and not review.operator_question:
                return "infra_blocked", review.reason or "Research infrastructure blocked progress."
            return "blocked", review.reason or "Reviewer blocked progress."
        if review.status == "replan_requested":
            return (
                "replan_requested",
                review.reason or "Reviewer requested a Manager-owned replacement plan.",
            )
        if review.status in {
            "research_incomplete",
            "paused_no_breakthrough",
            "exhausted_current_methods",
        }:
            return (
                cast(LoopStatus, review.status),
                review.reason
                or "Reviewer ended this research cycle without certifying success.",
            )
        if no_progress_streak >= no_progress_threshold:
            return (
                "no_progress",
                "Engineer produced no effective output for "
                f"{no_progress_streak} consecutive rounds.",
            )
        if (
            stall_threshold > 0
            and semantic_stall_streak >= stall_threshold
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                "Reviewer reported no decision progress for "
                f"{semantic_stall_streak} consecutive rounds.",
            )
        if (
            decision_timeout_seconds > 0
            and decision_idle_seconds >= decision_timeout_seconds
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                f"Reached {decision_timeout_seconds} seconds without decision progress.",
            )
        if (
            hard_escalate_rounds > 0
            and round_index >= hard_escalate_rounds
            and review.status == "continue"
        ):
            return (
                "blocked",
                f"Escalated: ran {round_index} rounds without completing — the "
                "mission is likely stuck on an external / unresolved constraint. "
                "Ending so the planner can re-plan or decompose. "
                + (review.reason or ""),
            )
        return None, ""


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
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because the engineer backend reported a transient "
            "infrastructure failure.\n"
            f"- Error: {error_text}\n"
            f"- Consecutive backend failures: {streak}/{threshold}\n"
        ),
        completion_summary_markdown="",
        failure_cause="environmental",
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
        round_summary_markdown=(
            "# Review Summary\n\n"
            f"- Reviewer skipped because `{stop_kind}` stopped the backend call.\n"
            f"- Error: {error_text}\n"
        ),
        completion_summary_markdown="",
        failure_cause="environmental",
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
        next_action="Select a model supported by the configured CLI, then retry.",
        operator_question=(
            "The configured model is unavailable. Choose a valid model in "
            "/config, then tell me to retry this task."
        ),
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because the selected model was rejected before "
            "a model turn started.\n"
            f"- Error: {error_text}\n"
        ),
        completion_summary_markdown="",
        failure_cause="environmental",
        backend_unavailable=True,
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
        reason=(
            "Engineer interrupted because daemon shutdown was requested; "
            f"no backend retry was attempted. error={error_text}"
        ),
        next_action=(
            "Restart the daemon when ready; the continuous planner will choose "
            "the next concrete task from the persisted project state."
        ),
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because daemon shutdown was requested.\n"
            f"- Error: {error_text}\n"
        ),
        completion_summary_markdown="",
        failure_cause="operator_interrupt",
    )


def operator_abort_review_decision(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> ReviewDecision:
    error_text = str(fatal_error or f"exit={exit_code}").strip()
    return ReviewDecision(
        status="blocked",
        reason=(
            "Engineer interrupted because the Manager decided, on the "
            f"operator's behalf, to abort this mission; error={error_text}"
        ),
        next_action=(
            "This item was intentionally aborted, not a crash — the daemon "
            "process itself keeps running and will continue with the next "
            "ready backlog item. Re-add this objective later if it still "
            "needs doing."
        ),
        round_summary_markdown=(
            "# Review Summary\n\n"
            "- Reviewer skipped because the Manager aborted this mission on "
            "the operator's behalf.\n"
            f"- Error: {error_text}\n"
        ),
        completion_summary_markdown="",
        failure_cause="operator_interrupt",
    )


__all__ = [
    "EngineerConfig",
    "SupervisedConfig",
    "SupervisedEngineer",
    "LoopOutcome",
    "backend_failure_review_decision",
    "external_pause_review_decision",
    "model_configuration_review_decision",
    "daemon_stop_review_decision",
    "operator_abort_review_decision",
    "fatal_error_looks_like_backend_failure",
    "runner_result_is_backend_failure",
    "fatal_error_looks_like_model_configuration",
    "fatal_error_looks_like_daemon_stop_request",
    "fatal_error_looks_like_operator_abort_request",
    "fatal_error_looks_like_effective_progress_timeout",
    "fatal_error_looks_like_compaction_thrash",
    "fatal_error_looks_like_recoverable_reconnect",
    "parse_continue_work_request",
    "should_clear_thread_id_after_outcome",
]
