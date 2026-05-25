"""SupervisedEngineer: round-loop wrapper around an engineer call.

This is the heart of the argus-skill v0.1 integration:

  * Each round, run the engineer with the current task prompt
    (initial task + optional skill block + optional reviewer next_action
    from prior round).
  * Run the user-provided acceptance checks (shell commands).
  * Call the reviewer to render a structured verdict.
  * If ``done``, stop. If ``continue``, capture ``next_action`` and loop.
    If ``blocked``, stop and surface the reason.

Provenance: the round-loop control flow is adapted from
``ArgusBot/codex_autoloop/core/engine.py`` (LoopEngine), simplified to the
single-agent case — argus-skill does not have ArgusBot's planner /
explore subagent; the skill block plays a similar "what to do" role for
the engineer in front of you.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Protocol

from ..core.models import (
    CheckResult,
    LoopOutcome,
    LoopStatus,
    ReviewDecision,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from .checks import all_checks_passed, run_checks
from .reviewer import Reviewer, ReviewerConfig

log = logging.getLogger(__name__)

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

def _fatal_error_looks_like_poisoned_session(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    return any(pattern in low for pattern in _POISONED_SESSION_FATAL_ERROR_PATTERNS)


def should_clear_thread_id_after_outcome(*, status: str, fatal_error: str | None) -> bool:
    """Return True when the carried Codex thread id should be cleared."""
    return (
        str(status).strip().casefold() == "no_progress"
        or _fatal_error_looks_like_poisoned_session(fatal_error)
    )


@dataclass
class EngineerConfig:
    model: str
    reasoning_effort: str | None = None
    extra_args: list[str] | None = None
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False


@dataclass
class SupervisedConfig:
    """Knobs for the round-loop control."""
    max_rounds: int = 500
    check_commands: list[str] = field(default_factory=list)
    check_timeout_seconds: int = 600
    no_progress_threshold: int = 2  # consecutive rounds with no engineer message before bailing
    session_id: str | None = None


class _AdvisoryLedger(Protocol):
    def render_advisory(self) -> str: ...


class SupervisedEngineer:
    """Run the engineer with reviewer-gated retries.

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
        engineer_prompt_builder: Callable[[str | None], str],
        supervised_config: SupervisedConfig,
        workdir: Path,
        on_event: Callable[[dict], None] | None = None,
        seed_thread_id: str | None = None,
        failed_tool_ledger: _AdvisoryLedger | None = None,
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action)`` is called once per round.
        On round 1, ``next_action`` is ``None``; on subsequent rounds,
        it is the reviewer's ``next_action`` from the previous round.
        The builder is responsible for assembling the full engineer
        prompt (task + skill block + injection text).

        Codex session continuity: round N+1 reuses round N's
        ``thread_id`` as ``resume_thread_id``. ``seed_thread_id`` (if
        provided) seeds round 1, allowing higher layers (e.g.
        life chat) to thread continuity *across* missions, not just
        across rounds.

        Returns ``(status, rounds, final_message, reason, last_thread_id)``.
        """
        rounds: list[RoundRecord] = []
        last_engineer_message = ""
        last_next_action: str | None = None
        no_progress_streak = 0
        current_thread_id: str | None = seed_thread_id

        for round_index in range(1, supervised_config.max_rounds + 1):
            engineer_prompt = engineer_prompt_builder(last_next_action)
            # Repeated-tool-failure interrupt: if the same tool/command has
            # failed multiple times this mission and we haven't yet
            # nudged the agent about it, splice an advisory at the top
            # of this round's prompt. The ledger tracks "already nudged"
            # so the warning fires only once per tool per mission, not
            # every subsequent round.
            if failed_tool_ledger is not None:
                try:
                    advisory = failed_tool_ledger.render_advisory()
                except Exception:  # noqa: BLE001 — ledger must never break the loop
                    advisory = ""
                if advisory:
                    engineer_prompt = advisory + "\n\n" + engineer_prompt
                    if on_event:
                        on_event({
                            "type": "engineer.failure_nudge",
                            "round": round_index,
                            "text": "repeated tool failures detected — advisory injected",
                        })
            if on_event:
                on_event({
                    "type": "round.start",
                    "round": round_index,
                    "round_max": supervised_config.max_rounds,
                    "text": f"engineer round {round_index}"
                            + (" (resuming codex session)" if current_thread_id else ""),
                })
            engineer_result = self._run_engineer(
                prompt=engineer_prompt,
                workdir=workdir,
                run_label=f"engineer-r{round_index}",
                resume_thread_id=current_thread_id,
            )
            # Capture thread_id so the next round (and the next mission,
            # via the return value) can resume the same codex session.
            new_tid = getattr(engineer_result, "thread_id", None)
            fatal_error = getattr(engineer_result, "fatal_error", None)
            round_thread_id = new_tid or current_thread_id
            engineer_message = engineer_result.last_agent_message or ""
            last_engineer_message = engineer_message or last_engineer_message

            # Phase-2 instrumentation: emit ``round.main.completed`` so the
            # supervisor's _CostTrackingSink can fold engineer-side token
            # counts into the iteration budget. Without this event the
            # cost sink only ever sees the reviewer half (and silently
            # under-charges) — leading to ``cost_usd=$0`` in the journal
            # when reviewer tokens were also missing pre-fix.
            if on_event:
                on_event({
                    "type": "round.main.completed",
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": round_thread_id,
                    "exit_code": getattr(engineer_result, "exit_code", 0),
                    "fatal_error": getattr(engineer_result, "fatal_error", None),
                    "last_message": engineer_message,
                    "input_tokens": int(getattr(engineer_result, "input_tokens", 0) or 0),
                    "cached_input_tokens": int(
                        getattr(engineer_result, "cached_input_tokens", 0) or 0
                    ),
                    "output_tokens": int(getattr(engineer_result, "output_tokens", 0) or 0),
                    "usage_scope": "delta",
                })

            if should_clear_thread_id_after_outcome(status="", fatal_error=fatal_error):
                current_thread_id = None
            elif new_tid:
                current_thread_id = new_tid

            if not engineer_message.strip():
                no_progress_streak += 1
            else:
                no_progress_streak = 0

            checks_results: list[CheckResult] = []
            if supervised_config.check_commands:
                checks_results = run_checks(
                    supervised_config.check_commands,
                    timeout_seconds=supervised_config.check_timeout_seconds,
                    cwd=str(workdir),
                )
                if on_event:
                    on_event({
                        "type": "checks.done",
                        "round": round_index,
                        "text": f"checks: {sum(1 for c in checks_results if c.passed)}/{len(checks_results)} pass",
                    })
            prev_round = rounds[-1] if rounds else None
            prev_review = getattr(prev_round, "review", None) if prev_round else None
            prev_review_summary = ""
            if prev_review is not None:
                prev_review_summary = (
                    getattr(prev_review, "round_summary_markdown", "")
                    or getattr(prev_review, "reason", "")
                    or ""
                )

            if on_event:
                on_event({
                    "type": "round.review.started",
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "session_id": supervised_config.session_id,
                })
            try:
                review = self.reviewer.evaluate(
                    objective=objective,
                    round_index=round_index,
                    session_id=supervised_config.session_id,
                    main_summary=engineer_message or "(no message)",
                    main_error=engineer_result.fatal_error,
                    checks=checks_results,
                    config=self.reviewer_config,
                    engineer_reasoning_summary=engineer_message or "",
                    prev_review_summary=prev_review_summary,
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"reviewer raised {type(exc).__name__}: {exc}"
                log.exception("reviewer raised during supervised round")
                review = ReviewDecision(
                    status="blocked",
                    confidence=0.0,
                    reason=msg,
                    next_action="Resolve the reviewer runner failure before retrying.",
                    round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                    completion_summary_markdown="",
                    failure_cause="environmental",
                )
            review = _coerce_review_for_failed_checks(review, checks_results)
            if on_event:
                on_event({
                    "type": "round.review.completed",
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "status": review.status,
                    "confidence": review.confidence,
                    "reason": review.reason,
                    "next_action": review.next_action,
                    "round_summary_markdown": getattr(review, "round_summary_markdown", "") or "",
                    "completion_summary_markdown": getattr(review, "completion_summary_markdown", "") or "",
                    "failure_cause": getattr(review, "failure_cause", "") or "",
                    "input_tokens": int(getattr(review, "input_tokens", 0) or 0),
                    "cached_input_tokens": int(
                        getattr(review, "cached_input_tokens", 0) or 0
                    ),
                    "output_tokens": int(getattr(review, "output_tokens", 0) or 0),
                    "usage_scope": "delta",
                    "text": f"review: {review.status} (conf={review.confidence:.2f}) — {review.reason}",
                })
            rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                checks=checks_results,
                review=review,
                fatal_error=engineer_result.fatal_error,
            ))

            terminal_status, reason = self._classify(
                review=review,
                checks_results=checks_results,
                no_progress_streak=no_progress_streak,
                no_progress_threshold=supervised_config.no_progress_threshold,
                round_index=round_index,
                max_rounds=supervised_config.max_rounds,
            )
            if terminal_status is not None:
                return (
                    terminal_status,
                    rounds,
                    last_engineer_message,
                    reason,
                    None
                    if should_clear_thread_id_after_outcome(
                        status=terminal_status,
                        fatal_error=fatal_error,
                    )
                    else current_thread_id,
                )

            last_next_action = review.next_action

        return (
            "max_rounds",
            rounds,
            last_engineer_message,
            f"Hit max_rounds={supervised_config.max_rounds} without reviewer-confirmed completion.",
            current_thread_id,
        )

    def _run_engineer(
        self,
        *,
        prompt: str,
        workdir: Path,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        try:
            return self.engineer_runner.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=self.engineer_config.model,
                    reasoning_effort=self.engineer_config.reasoning_effort,
                    extra_args=self.engineer_config.extra_args,
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    working_dir=str(workdir),
                ),
                run_label=run_label,
                resume_thread_id=resume_thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"engineer runner raised {type(exc).__name__}: {exc}"
            log.exception("engineer runner raised during %s", run_label)
            return RunnerResult(
                exit_code=-1,
                fatal_error=msg,
                stderr_lines=[msg],
            )

    @staticmethod
    def _classify(
        *,
        review: ReviewDecision,
        checks_results: list[CheckResult],
        no_progress_streak: int,
        no_progress_threshold: int,
        round_index: int,
        max_rounds: int,  # noqa: ARG004 — kept for API symmetry / future heuristics
    ) -> tuple[LoopStatus | None, str]:
        if review.status == "done" and (not checks_results or all_checks_passed(checks_results)):
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status == "blocked":
            return "blocked", review.reason or "Reviewer blocked progress."
        if no_progress_streak >= no_progress_threshold:
            return (
                "no_progress",
                f"Engineer produced no output for {no_progress_streak} consecutive rounds.",
            )
        # done but checks failed — treat as continue (reviewer was wrong /
        # checks discovered residual gap).
        if review.status == "done" and checks_results and not all_checks_passed(checks_results):
            log.info(
                "round %d: reviewer said done but %d/%d checks failed; "
                "continuing",
                round_index,
                sum(1 for c in checks_results if not c.passed),
                len(checks_results),
            )
        return None, ""


def _fallback_failed_check_handoff(checks: list[CheckResult]) -> str:
    failed = [check for check in checks if not check.passed]
    if not failed:
        return ""

    lines: list[str] = [
        "The acceptance checks still fail. Convert the validator blockers into concrete fixes, "
        "then rerun the exact failed command before claiming completion.",
    ]
    for index, check in enumerate(failed, start=1):
        lines.append(f"{index}. `{check.command}` exited {check.exit_code}.")
    return "\n".join(lines)


def _coerce_review_for_failed_checks(
    review: ReviewDecision,
    checks: list[CheckResult],
) -> ReviewDecision:
    failed = [check for check in checks if not check.passed]
    if not failed:
        return review

    next_action = (review.next_action or "").strip()
    if review.status != "done":
        return replace(review, next_action=next_action or _fallback_failed_check_handoff(failed))

    if not next_action or next_action.casefold().startswith("no further action"):
        next_action = _fallback_failed_check_handoff(failed)
    failed_commands = ", ".join(f"`{check.command}` exited {check.exit_code}" for check in failed)
    return replace(
        review,
        status="continue",
        reason=(
            "Acceptance checks failed after the engineer turn, so the task cannot be done: "
            f"{failed_commands}."
        ),
        next_action=next_action,
    )


__all__ = [
    "EngineerConfig",
    "SupervisedConfig",
    "SupervisedEngineer",
    "LoopOutcome",
    "should_clear_thread_id_after_outcome",
]
