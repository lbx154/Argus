"""Round-loop phase: engineer turn execution + non-review stop shortcircuits.

Owns running one fresh Engineer provider-session turn (prompt in, parsed
``RunnerResult`` out, plus the secret-guard scrub, long-job-ownership check,
and ``round.main.completed`` accounting that always happen regardless of
outcome), and then the non-review-worthy stop-kind shortcircuits that must
end or retry the round WITHOUT invoking the Reviewer: daemon shutdown,
operator abort, model misconfiguration, an external pause (budget/provider
condition), a permanent backend error, and a transient backend failure that
retries in a fresh session up to ``backend_failure_threshold``. The Reviewer
remains the sole completion authority for every other outcome; this phase
only ever returns a terminal result, retries the SAME round via
``continue_loop``, or lets the round proceed to the reviewed path.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..core.event_catalog import EventType
from ..core.models import RoundRecord
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..core.stop_kinds import (
    NON_FAILURE_STOP_KINDS,
    normalize_stop_kind,
    pause_status_for_stop_kind,
    stop_kind_from_external_interrupt,
)
from .round_signals import _apply_round_secret_guard, _review_event_payload
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
    control_return,
)
from .round_stop_signals import (
    backend_failure_review_decision,
    daemon_stop_review_decision,
    external_pause_review_decision,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_model_configuration,
    fatal_error_looks_like_operator_abort_request,
    model_configuration_review_decision,
    operator_abort_review_decision,
    runner_result_is_backend_failure,
)
from .self_review import (
    parse_engineer_completion_decision,
    read_engineer_completion_decision,
    strip_legacy_engineer_control,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


class RoundExecutionMixin:
    """Mixin providing ``SupervisedEngineer``'s engineer-turn-execution phase."""

    def _run_engineer_turn(
        self,
        *,
        round_index: int,
        engineer_prompt: str,
        workdir: Path,
        supervised_config: "SupervisedConfig",
        checkpoint_path: Path | None,
        control_path: Path,
        on_event: Callable[[dict], None] | None,
        state: RoundLoopState,
    ) -> EngineerTurnOutcome:
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
        completion_decision = read_engineer_completion_decision(control_path)
        if completion_decision is None:
            completion_decision = parse_engineer_completion_decision(
                engineer_message
            )
            if completion_decision is not None:
                engineer_message = strip_legacy_engineer_control(
                    engineer_message
                )
        if supervised_config.context_packet_path:
            try:
                from ..life.context_packet import record_engineer_handoff

                record_engineer_handoff(
                    mission_context_path=supervised_config.context_packet_path,
                    round_index=round_index,
                    engineer_summary=engineer_message,
                    checkpoint_path=checkpoint_path,
                    control_path=control_path,
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
            and secret_guard_reviewer_note not in state.pending_secret_guard_notes
        ):
            state.pending_secret_guard_notes.append(secret_guard_reviewer_note)
            del state.pending_secret_guard_notes[:-8]
        state.last_engineer_message = engineer_message or state.last_engineer_message
        orphan_group_id = int(
            getattr(engineer_result, "orphan_process_group_id", 0) or 0
        )
        process_ownership_note = ""
        if orphan_group_id:
            cleanup_succeeded = bool(
                getattr(
                    engineer_result,
                    "orphan_process_group_cleanup_succeeded",
                    False,
                )
            )
            process_ownership_note = (
                "ARGUS PROCESS OWNERSHIP FACT: the provider turn exited while "
                f"descendants remained in its private process group {orphan_group_id}. "
                "The runner targeted only that exact group for cleanup "
                f"(cleanup_succeeded={str(cleanup_succeeded).lower()}). Durable "
                "Argus subagents run in separately owned process groups. Treat this "
                "as an orchestration fact, not as scientific evidence."
            )
            if on_event:
                on_event({
                    "type": "round.orphan_process_group",
                    "round_index": round_index,
                    "process_group_id": orphan_group_id,
                    "cleanup_succeeded": cleanup_succeeded,
                    "operator_alert": True,
                    "text": process_ownership_note,
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

        return EngineerTurnOutcome(
            engineer_result=engineer_result,
            round_thread_id=round_thread_id,
            fatal_error=fatal_error,
            safe_fatal_error=safe_fatal_error,
            stop_kind=stop_kind,
            raw_engineer_message=raw_engineer_message,
            engineer_message=engineer_message,
            completion_decision=completion_decision,
            process_ownership_note=process_ownership_note,
            round_started_at=round_started_at,
        )

    def _handle_stop_kind_shortcircuit(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        fatal_error = outcome.fatal_error
        stop_kind = outcome.stop_kind
        engineer_message = outcome.engineer_message
        round_thread_id = outcome.round_thread_id
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
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind="daemon_shutdown",
            ))
            return control_return((
                "paused_daemon_shutdown",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                None,
            ))

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
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind="operator_abort",
            ))
            return control_return((
                "aborted",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                None,
            ))

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
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
            ))
            return control_return((
                "blocked",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                None,
            ))

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
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind=stop_kind,
            ))
            return control_return((
                pause_status,
                state.rounds,
                state.last_engineer_message,
                review.reason,
                round_thread_id,
            ))

        if stop_kind == "permanent_error":
            review = backend_failure_review_decision(
                fatal_error=fatal_error,
                exit_code=getattr(engineer_result, "exit_code", 0),
                streak=1,
                threshold=1,
            )
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind=stop_kind,
            ))
            return control_return((
                "error",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                None,
            ))

        if runner_result_is_backend_failure(engineer_result):
            state.backend_failure_streak += 1
            state.no_progress_streak = 0
            configured_threshold = max(
                1, int(supervised_config.backend_failure_threshold or 1)
            )
            fatal_error_low = str(fatal_error or "").casefold()
            watchdog_failure = (
                "forced restart after hard idle timeout" in fatal_error_low
                or "acp hard idle timeout" in fatal_error_low
            )
            threshold = (
                min(configured_threshold, 2)
                if watchdog_failure
                else configured_threshold
            )
            review = backend_failure_review_decision(
                fatal_error=fatal_error,
                exit_code=getattr(engineer_result, "exit_code", 0),
                streak=state.backend_failure_streak,
                threshold=threshold,
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
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind=stop_kind,
            ))
            if watchdog_failure and on_event:
                exhausted = (
                    state.backend_failure_streak >= threshold
                    or round_index >= supervised_config.max_rounds
                )
                checkpoint_path = supervised_config.checkpoint_path
                try:
                    checkpoint_available = bool(
                        checkpoint_path is not None and Path(checkpoint_path).exists()
                    )
                except OSError:
                    checkpoint_available = False
                on_event({
                    "type": (
                        "round.watchdog.retry_exhausted"
                        if exhausted
                        else "round.watchdog.retry"
                    ),
                    "round_index": round_index,
                    "attempt": state.backend_failure_streak,
                    "max_attempts": threshold,
                    "fresh_session": True,
                    "checkpoint_path": (
                        str(checkpoint_path) if checkpoint_path is not None else ""
                    ),
                    "checkpoint_available": checkpoint_available,
                    "operator_alert": True,
                    "fatal_error": fatal_error,
                })
            if state.backend_failure_streak >= threshold or round_index >= supervised_config.max_rounds:
                return control_return((
                    "error",
                    state.rounds,
                    state.last_engineer_message,
                    review.reason,
                    None,
                ))
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
            return control_continue_loop()
        return control_proceed()
