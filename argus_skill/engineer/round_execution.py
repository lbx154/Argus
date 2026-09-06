"""Round-loop phase: engineer turn execution + non-review stop shortcircuits.

Owns running one Engineer provider-session turn (fresh or safely resumed;
prompt in, parsed ``RunnerResult`` out), plus the secret-guard scrub,
long-job-ownership check, and ``round.main.completed`` accounting that always
happen regardless of
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
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..core.models import RoundRecord
from ..core.role_decision import latest_role_decision
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
    BACKEND_FAILURE_SAME_CAUSE_THRESHOLD,
    authentication_review_decision,
    backend_failure_hold_backoff_seconds,
    backend_failure_review_decision,
    backend_failure_signature,
    daemon_stop_review_decision,
    external_pause_review_decision,
    fatal_error_looks_like_auth_failure,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_model_configuration,
    fatal_error_looks_like_operator_abort_request,
    fatal_error_looks_like_provider_turn_cap,
    model_configuration_review_decision,
    operator_abort_review_decision,
    provider_turn_cap_review_decision,
    runner_result_is_backend_failure,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)

# Consecutive Engineer calls allowed to each use their whole per-call
# provider-turn allowance before the mission stops. Each capped call already
# spends a full allowance of provider turns, so a run of them is the very
# spend pattern the allowance exists to end.
_PROVIDER_TURN_CAP_STREAK_LIMIT = 3

# The longest the backend-failure hold sleeps between checks of the daemon's
# stop and abort signals. The hold itself can grow to the hour scale; a
# shutdown or an abort must not wait behind it.
_BACKEND_FAILURE_HOLD_SLICE_SECONDS = 10.0


def _engineer_decision_message(payload: dict) -> str:
    """Render a process decision for existing round-control consumers."""
    status = str(payload.get("status", "") or "").strip().lower()
    result = str(
        payload.get("result", payload.get("summary", "")) or ""
    ).strip()
    default_owner = "reviewer" if status == "done" else "engineer"
    lines = [
        result,
        f"MILESTONE_STATUS={'done' if status == 'done' else 'continue'}",
        f"NEXT_OWNER={str(payload.get('next_owner', default_owner) or default_owner)}",
    ]
    question = str(payload.get("operator_question", "") or "").strip()
    if question:
        lines.append(f"OPERATOR_QUESTION={question}")
    options = payload.get("operator_options")
    if isinstance(options, list) and options:
        rendered_options = []
        for option in options:
            if isinstance(option, dict):
                rendered_options.append(
                    " :: ".join(
                        str(option.get(field, "") or "").strip()
                        for field in ("id", "label", "description")
                    )
                )
            else:
                rendered_options.append(str(option))
        lines.append(
            "OPERATOR_OPTIONS="
            + " || ".join(rendered_options)
        )
    return "\n".join(line for line in lines if line)


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
        resume_thread_id: str | None,
        on_event: Callable[[dict], None] | None,
        state: RoundLoopState,
    ) -> EngineerTurnOutcome:
        from ..core.operator_context import operator_context_revision_from_text

        operator_context_revision = operator_context_revision_from_text(engineer_prompt)
        round_started_at = time.time()
        engineer_result, _round_compactions = self._run_engineer(
            prompt=engineer_prompt,
            workdir=workdir,
            run_label=f"engineer-r{round_index}",
            resume_thread_id=resume_thread_id,
            reasoning_effort=(
                self.engineer_config.initial_reasoning_effort
                if round_index == 1
                else self.engineer_config.reasoning_effort
            ),
            supervised_config=supervised_config,
            on_event=on_event,
        )
        new_tid = engineer_result.thread_id
        fatal_error = engineer_result.fatal_error
        safe_fatal_error = redact_secrets_text(
            str(fatal_error or ""),
            known_values=known_secret_values(),
        ) or None
        stop_kind = normalize_stop_kind(
            engineer_result.stop_kind
        ) or stop_kind_from_external_interrupt(fatal_error)
        round_thread_id = new_tid
        process_decision = latest_role_decision(engineer_result, "engineer")
        raw_engineer_message = (
            _engineer_decision_message(process_decision)
            if process_decision is not None
            else (engineer_result.last_agent_message or "")
        )
        engineer_message = redact_secrets_text(
            raw_engineer_message,
            known_values=known_secret_values(),
        )
        engineer_session = state.engineer_session
        if engineer_session is None:
            raise RuntimeError("engineer role session was not initialized")
        session_metadata_persisted = engineer_session.complete(
            engineer_result,
            decisive_output=engineer_message,
        )
        if on_event:
            on_event({
                "type": EventType.ROLE_SESSION_TURN,
                "role": "engineer",
                "policy": engineer_session.policy,
                "action": engineer_session.action,
                "rotation_reason": engineer_session.rotation_reason,
                "round_index": round_index,
                "session_id": str(new_tid or ""),
                "turns_on_session": engineer_session.turns,
                "input_tokens": int(engineer_result.input_tokens or 0),
                "cached_input_tokens": int(engineer_result.cached_input_tokens or 0),
                "duration_ms": int((time.time() - round_started_at) * 1000),
                "prompt_chars": len(engineer_prompt),
                "prompt_estimated_tokens": (len(engineer_prompt) + 3) // 4,
                "capsule_path": str(engineer_session.path or ""),
                "metadata_persisted": session_metadata_persisted,
                "persistence_warning": engineer_session.persistence_error,
                "operator_context_revision": operator_context_revision,
            })
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
            and secret_guard_reviewer_note not in state.pending_secret_guard_notes
        ):
            state.pending_secret_guard_notes.append(secret_guard_reviewer_note)
            del state.pending_secret_guard_notes[:-8]
        state.last_engineer_message = engineer_message or state.last_engineer_message
        orphan_group_id = int(engineer_result.orphan_process_group_id or 0)
        process_ownership_note = ""
        if orphan_group_id:
            cleanup_succeeded = bool(engineer_result.orphan_process_group_cleanup_succeeded)
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
                "exit_code": engineer_result.exit_code,
                "fatal_error": safe_fatal_error,
                "stop_kind": stop_kind,
                "last_message": engineer_message,
                "input_tokens": int(engineer_result.input_tokens or 0),
                "cached_input_tokens": int(engineer_result.cached_input_tokens or 0),
                "output_tokens": int(engineer_result.output_tokens or 0),
                "reasoning_output_tokens": int(engineer_result.reasoning_output_tokens or 0),
                "premium_requests": float(engineer_result.premium_requests or 0.0),
                "usage_scope": "delta",
                "operator_context_revision": operator_context_revision,
            })

        return EngineerTurnOutcome(
            engineer_result=engineer_result,
            round_thread_id=round_thread_id,
            fatal_error=fatal_error,
            safe_fatal_error=safe_fatal_error,
            stop_kind=stop_kind,
            raw_engineer_message=raw_engineer_message,
            engineer_message=engineer_message,
            process_ownership_note=process_ownership_note,
            round_started_at=round_started_at,
            decision=process_decision,
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
        engineer_session = state.engineer_session
        if engineer_session is None:
            raise RuntimeError("engineer role session was not initialized")
        if not fatal_error_looks_like_provider_turn_cap(fatal_error):
            # Any other ending — success, pause, or failure — breaks a run of
            # allowance-capped calls.
            state.provider_turn_cap_streak = 0
        if (
            stop_kind == "daemon_shutdown"
            or fatal_error_looks_like_daemon_stop_request(fatal_error)
        ):
            review = daemon_stop_review_decision(
                fatal_error=fatal_error,
                exit_code=engineer_result.exit_code,
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
                exit_code=engineer_result.exit_code,
                engineer_aborted_before_review=True,
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
            engineer_session.rotate("model_configuration")
            review = model_configuration_review_decision(
                fatal_error=fatal_error,
                exit_code=engineer_result.exit_code,
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
            # "Model X is not available" is usually the provider having a bad
            # minute, not a misconfiguration: one such outage on 2026-09-05
            # marked eight queued missions blocked inside two minutes. Pause
            # the mission like any provider cooldown so the daemon backs off
            # and retries it, instead of consuming the backlog. The operator
            # alert above still fires on every attempt, so a real typo in the
            # model name stays visible.
            state.rounds.append(RoundRecord(
                round_index=round_index,
                engineer_message=engineer_message,
                engineer_exit_code=engineer_result.exit_code,
                review=review,
                fatal_error=engineer_result.fatal_error,
                stop_kind="provider_cooldown",
            ))
            return control_return((
                "paused_provider_cooldown",
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
                exit_code=engineer_result.exit_code,
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
            engineer_session.rotate("permanent_error")
            auth_failure = fatal_error_looks_like_auth_failure(fatal_error)
            review = (
                authentication_review_decision(
                    fatal_error=fatal_error,
                    exit_code=engineer_result.exit_code,
                )
                if auth_failure
                else backend_failure_review_decision(
                    fatal_error=fatal_error,
                    exit_code=engineer_result.exit_code,
                    streak=1,
                    threshold=1,
                )
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
                "blocked" if auth_failure else "error",
                state.rounds,
                state.last_engineer_message,
                review.reason,
                None,
            ))

        if fatal_error_looks_like_provider_turn_cap(fatal_error):
            return self._handle_provider_turn_cap_restart(
                round_index=round_index,
                supervised_config=supervised_config,
                outcome=outcome,
                state=state,
                on_event=on_event,
            )

        if runner_result_is_backend_failure(engineer_result):
            engineer_session.rotate("backend_failure")
            state.backend_failure_streak += 1
            signature = backend_failure_signature(
                fatal_error,
                exit_code=engineer_result.exit_code,
            )
            if signature and signature == state.backend_failure_signature:
                state.backend_failure_same_cause_streak += 1
            else:
                state.backend_failure_signature = signature
                state.backend_failure_same_cause_streak = 1
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
            # A run of IDENTICAL failures is one continuing outage or one
            # standing misconfiguration, not N independent accidents. Failing
            # the mission at the threshold hands it to the planner, which pays
            # for a replan and redispatches into the same failure — 353 such
            # error outcomes cost $123 in one 48-hour window. Hold instead:
            # keep retrying this same round with a backoff that grows to the
            # hour scale once the cause has repeated
            # BACKEND_FAILURE_SAME_CAUSE_THRESHOLD times, with an
            # operator-visible event on every held retry. Watchdog restarts
            # keep their stricter budget: each of those retries replays a full
            # hung turn, which is exactly the spend this hold exists to avoid.
            same_cause_hold = (
                not watchdog_failure
                and state.backend_failure_same_cause_streak >= 2
            )
            review = backend_failure_review_decision(
                fatal_error=fatal_error,
                exit_code=engineer_result.exit_code,
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
                    or (
                        supervised_config.max_rounds > 0
                        and round_index >= supervised_config.max_rounds
                    )
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
            if (
                state.backend_failure_streak >= threshold and not same_cause_hold
            ) or (
                supervised_config.max_rounds > 0
                and round_index >= supervised_config.max_rounds
            ):
                return control_return((
                    "error",
                    state.rounds,
                    state.last_engineer_message,
                    review.reason,
                    None,
                ))
            base_backoff_seconds = max(
                0.0, float(supervised_config.backend_failure_backoff_seconds or 0.0)
            )
            circuit_open = (
                same_cause_hold
                and state.backend_failure_same_cause_streak
                >= BACKEND_FAILURE_SAME_CAUSE_THRESHOLD
            )
            backoff_seconds = (
                backend_failure_hold_backoff_seconds(
                    same_cause_streak=state.backend_failure_same_cause_streak,
                    base_backoff_seconds=base_backoff_seconds,
                )
                if circuit_open
                else base_backoff_seconds
            )
            if backoff_seconds:
                if on_event:
                    error_text = str(fatal_error or "").strip()
                    on_event({
                        "type": "round.backend_failure.backoff",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "seconds": backoff_seconds,
                        "same_cause_streak": (
                            state.backend_failure_same_cause_streak
                        ),
                        "signature": state.backend_failure_signature,
                        "operator_alert": circuit_open,
                        "text": (
                            (
                                "The backend has failed the same way "
                                f"{state.backend_failure_same_cause_streak} "
                                f"times in a row ({error_text}). Waiting "
                                f"{backoff_seconds:.0f}s before the next "
                                "attempt so a standing failure stops costing "
                                "money; if this is a configuration problem, "
                                "fix it or stop the mission."
                            )
                            if circuit_open
                            else (
                                "backend failure; retrying in a fresh Codex "
                                f"session after {backoff_seconds:.1f}s"
                            )
                        ),
                    })
                interrupt_reason = self._hold_before_backend_failure_retry(
                    backoff_seconds
                )
                if interrupt_reason:
                    interrupt_kind = stop_kind_from_external_interrupt(
                        interrupt_reason
                    )
                    status = (
                        "aborted"
                        if interrupt_kind == "operator_abort"
                        else pause_status_for_stop_kind(interrupt_kind)
                        or "paused_operator"
                    )
                    reason_text = (
                        "The wait after a repeated backend failure ended "
                        f"early: {interrupt_reason}."
                    )
                    if on_event:
                        on_event({
                            "type": "round.backend_failure.hold_interrupted",
                            "round_index": round_index,
                            "round_max": supervised_config.max_rounds,
                            "stop_kind": interrupt_kind or "",
                            "text": reason_text,
                        })
                    return control_return((
                        status,
                        state.rounds,
                        state.last_engineer_message,
                        reason_text,
                        None,
                    ))
            return control_continue_loop()
        return control_proceed()

    def _hold_interrupt_reason(self) -> str | None:
        """The daemon's stop or the operator's abort, read during a hold.

        The engineer backend already consults this provider during a live
        provider call (``AgentCliBackend`` composes its default interrupt
        reason provider, wired to the daemon's stop event and the operator's
        abort mailbox, into every run). The hold between failed rounds must
        consult it too: the backoff can grow to the hour scale, and a shutdown
        must not wait behind it. A runner without the attribute (tests, bare
        backends) makes this a no-op.
        """
        provider = getattr(
            self.engineer_runner, "_default_interrupt_reason_provider", None
        )
        if not callable(provider):
            return None
        try:
            reason = provider()
        except Exception:  # noqa: BLE001 — a provider fault must never wedge the hold
            return None
        text = str(reason or "").strip()
        return text or None

    def _hold_before_backend_failure_retry(self, seconds: float) -> str | None:
        """Sleep out a backend-failure backoff, waking for stop signals.

        Returns the interrupt reason when the daemon asked to stop or the
        operator asked to abort during the wait, and ``None`` when the wait
        ran its full course.
        """
        remaining = max(0.0, float(seconds))
        while True:
            reason = self._hold_interrupt_reason()
            if reason:
                return reason
            if remaining <= 0:
                return None
            slice_seconds = min(remaining, _BACKEND_FAILURE_HOLD_SLICE_SECONDS)
            time.sleep(slice_seconds)
            remaining -= slice_seconds

    def _handle_provider_turn_cap_restart(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        """Continue a task whose Engineer call used its whole turn allowance.

        Not a failure path. The runner ended the call at the per-call
        provider-turn allowance (see ``ARGUS_SKILL_PROVIDER_TURN_CAP``); every
        further turn would have resent the whole grown transcript. This phase
        asks the same conversation to finish cleanly — write the checkpoint,
        reply with a summary — then rotates to a fresh session and lets the
        next round continue from that summary plus the checkpoint.
        """
        engineer_result = outcome.engineer_result
        engineer_session = state.engineer_session
        if engineer_session is None:
            raise RuntimeError("engineer role session was not initialized")
        state.provider_turn_cap_streak += 1
        checkpoint_path = supervised_config.checkpoint_path
        wind_down_summary = ""
        wind_down_usage: dict[str, int] = {}
        if outcome.round_thread_id and not self.engineer_config.isolate_workdir:
            wind_down_result = self._run_provider_turn_cap_wind_down(
                round_index=round_index,
                thread_id=str(outcome.round_thread_id),
                workdir=Path(engineer_session.workdir),
                checkpoint_path=checkpoint_path,
                supervised_config=supervised_config,
                on_event=on_event,
            )
            wind_down_summary = redact_secrets_text(
                str(wind_down_result.last_agent_message or "")[:2000],
                known_values=known_secret_values(),
            ).strip()
            wind_down_usage = {
                "input_tokens": int(wind_down_result.input_tokens or 0),
                "cached_input_tokens": int(
                    wind_down_result.cached_input_tokens or 0
                ),
                "output_tokens": int(wind_down_result.output_tokens or 0),
            }
        if not wind_down_summary:
            # No resumable conversation (or it declined to answer): the best
            # summary is what the capped call had already said out loud.
            wind_down_summary = str(outcome.engineer_message or "")[:2000].strip()
        engineer_session.rotate("provider_turn_cap")
        review = provider_turn_cap_review_decision(
            fatal_error=outcome.fatal_error,
            exit_code=engineer_result.exit_code,
            wind_down_summary=wind_down_summary,
            streak=state.provider_turn_cap_streak,
            streak_limit=_PROVIDER_TURN_CAP_STREAK_LIMIT,
        )
        try:
            checkpoint_available = bool(
                checkpoint_path is not None and Path(checkpoint_path).exists()
            )
        except OSError:
            checkpoint_available = False
        if on_event:
            on_event({
                "type": "round.provider_turn_cap.restart",
                "round_index": round_index,
                "round_max": supervised_config.max_rounds,
                "streak": state.provider_turn_cap_streak,
                "streak_limit": _PROVIDER_TURN_CAP_STREAK_LIMIT,
                "checkpoint_path": (
                    str(checkpoint_path) if checkpoint_path is not None else ""
                ),
                "checkpoint_available": checkpoint_available,
                "wind_down_summary_chars": len(wind_down_summary),
                **wind_down_usage,
                "text": (
                    f"Round {round_index}: the Engineer's session used its "
                    "whole per-call provider-turn allowance — a routine pause "
                    "for housekeeping, not an error. Argus asked it to leave a "
                    "checkpoint and a summary, and continues the same task in "
                    "a fresh session."
                ),
            })
            on_event(_review_event_payload(
                review,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                text=(
                    "review: skipped (per-call provider-turn allowance "
                    "reached; continuing in a fresh session)"
                ),
                review_skipped=True,
            ))
        state.rounds.append(RoundRecord(
            round_index=round_index,
            engineer_message=outcome.engineer_message,
            engineer_exit_code=engineer_result.exit_code,
            review=review,
            fatal_error=engineer_result.fatal_error,
        ))
        state.last_engineer_message = (
            wind_down_summary or state.last_engineer_message
        )
        if state.provider_turn_cap_streak >= _PROVIDER_TURN_CAP_STREAK_LIMIT:
            return control_return((
                "error",
                state.rounds,
                state.last_engineer_message,
                (
                    f"{state.provider_turn_cap_streak} Engineer sessions in a "
                    "row each used their whole per-call provider-turn "
                    "allowance without a completed turn. Stopping so the "
                    "operator can rescope the task or raise "
                    "ARGUS_SKILL_PROVIDER_TURN_CAP."
                ),
                None,
            ))
        state.reviewer_next_action = review.next_action
        # A call that ended at its turn allowance was not a backend failure,
        # so it ends any run of identical failures — the same reset the
        # self-review phase applies after an ordinary completed round, which
        # this continue skips. The same-cause count restarts from one if that
        # signature ever returns; without this reset, a rate limit after the
        # restart would read as the continuation of an outage that ended
        # rounds ago and open the hold on an isolated accident.
        state.backend_failure_streak = 0
        state.backend_failure_signature = ""
        state.backend_failure_same_cause_streak = 0
        return control_continue_loop()

    def _run_provider_turn_cap_wind_down(
        self,
        *,
        round_index: int,
        thread_id: str,
        workdir: Path,
        checkpoint_path: Path | None,
        supervised_config: "SupervisedConfig",
        on_event: Callable[[dict], None] | None,
    ):
        """One short resumed call: write the checkpoint, reply with a summary.

        The wind-down resumes the very conversation that used its allowance, so
        the runner grants it only a small allowance of its own (the
        ``.winddown`` label — see ``agent_cli._env._provider_turn_cap``).
        Whatever it returns, the mission continues; an empty or failed
        wind-down only means the fresh session starts from the checkpoint and
        the capped call's last streamed message.
        """
        checkpoint_line = (
            f"1) Write or update the continuation note at `{checkpoint_path}` "
            "with the current state, the evidence paths that matter, anything "
            "standing in the way, and the single next action. "
            if checkpoint_path is not None
            else "1) Record the current state in your reply. "
        )
        prompt = (
            "This working session has used its per-call provider-turn "
            "allowance, so the harness will continue the task in a fresh "
            "session. Nothing failed; your work is kept. Finish cleanly now: "
            + checkpoint_line
            + "2) Reply with a summary of at most 200 words: what is done, "
            "what is in flight, and what the fresh session should do first. "
            "Do not start new work."
        )
        result, _compactions = self._run_engineer(
            prompt=prompt,
            workdir=workdir,
            run_label=f"engineer-r{round_index}.winddown",
            resume_thread_id=thread_id,
            reasoning_effort=self.engineer_config.reasoning_effort,
            supervised_config=supervised_config,
            on_event=on_event,
        )
        return result
