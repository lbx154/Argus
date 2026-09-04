"""Round-loop phase: Reviewer invocation and infra-only retry.

Owns calling the independent Reviewer for the current round and retrying
ONLY the reviewer leg on an infra flake (subprocess crash or missing verdict
schema) — never discarding the Engineer's already-valid output and never
re-running the (expensive) Engineer turn just because the cheap Reviewer call
hiccuped. Reviewer backend death must never be laundered
into a silent ``continue``: it is routed through the same transient-backoff +
escalate-to-error machinery the Engineer backend-failure path uses, so the
harness can never run the sole completion gate blind. Once a real (non
backend-failure) verdict is obtained, this phase hands the ``ReviewDecision``
to the round-settlement phase via ``RoundControl.payload``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..core.models import ReviewDecision, RoundRecord
from ..core.stop_kinds import (
    NON_FAILURE_STOP_KINDS,
    normalize_stop_kind,
    pause_status_for_stop_kind,
    stop_kind_from_external_interrupt,
)
from ..life.context_packet import render_mission_brief
from .external_work import render_external_work_advisory
from .round_signals import _review_event_payload
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_proceed,
    control_return,
)
from .round_stop_signals import (
    daemon_stop_review_decision,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_operator_abort_request,
    operator_abort_review_decision,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


def _previous_review_summary(state: RoundLoopState) -> str:
    """Render the last three verdicts so repetition is visible to Reviewer."""
    if not state.rounds:
        return ""
    lines: list[str] = []
    for record in state.rounds[-3:]:
        review = record.review
        status = " ".join(str(review.status or "").split()) or "unknown"
        reason = " ".join(str(review.reason or "").split()) or "(no reason)"
        lines.append(
            f"Round {record.round_index} — {status}: {reason[:600]}"
        )
    return "\n".join(lines)


def _active_manager_directive_for_reviewer(
    supervised_config: "SupervisedConfig",
) -> list[str]:
    """Load the Reviewer-trimmed projection from the one operator store."""
    candidates: list[Path] = []
    if supervised_config.engineer_log_path:
        candidates.append(Path(supervised_config.engineer_log_path).expanduser().parent)
    if supervised_config.context_packet_path:
        packet = Path(supervised_config.context_packet_path).expanduser()
        if len(packet.parents) >= 3:
            candidates.append(packet.parents[2])
    from ..core.operator_context import build_operator_context_block

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            root = candidate.resolve()
        except OSError:
            root = candidate
        if root in seen:
            continue
        seen.add(root)
        message, _revision = build_operator_context_block("reviewer", root)
        if message:
            return [message]
    return []


class RoundReviewerMixin:
    """Mixin providing ``SupervisedEngineer``'s reviewer-invocation phase."""

    def _call_reviewer_once(
        self,
        *,
        objective: str,
        original_objective: str | None,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        scope: str,
        checkpoint_path: Path | None,
        reviewer_skill_block: str | None,
        escalate_hint: str,
        engineer_result,
        engineer_message: str,
        safe_fatal_error: str | None,
        process_ownership_note: str,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> ReviewDecision:
        """Call the Reviewer once; direct project-wiki edits are durable output."""
        operator_messages = _active_manager_directive_for_reviewer(
            supervised_config
        )
        from ..core.operator_context import operator_context_revision_from_text

        operator_context_revision = operator_context_revision_from_text(
            "\n".join(operator_messages)
        )
        reviewer_background_context = ""
        if supervised_config.background_subagent_advisory:
            try:
                reviewer_background_context = render_external_work_advisory(
                    workdir,
                    include_subagents=True,
                )
            except Exception:  # noqa: BLE001 — advisory is non-critical context
                log.debug("reviewer subagent advisory refresh failed", exc_info=True)
        reviewer_session = state.reviewer_session
        reviewer_resume_id = (
            reviewer_session.prepare(
                max_turns=supervised_config.role_session_max_turns,
                max_input_tokens=supervised_config.role_session_max_input_tokens,
            )
            if reviewer_session is not None
            else None
        )
        capsule_block = reviewer_session.prompt_block() if reviewer_session else ""
        rotation_block = ""
        if (
            reviewer_session is not None
            and round_index > 1
            and reviewer_session.policy != "fresh"
            and reviewer_session.action in {"fresh", "rotated"}
        ):
            rotation_block = (
                "## Reviewer session rotation — judge the current round\n"
                f"This is Reviewer round {round_index}, not round 1. Provider context "
                "was rotated. Do not reenact an earlier Engineer stage, create its "
                "artifacts, or ask for an approval already recorded in the canonical "
                "checkpoint. Verify the current Engineer summary and artifacts, then "
                "return the verdict for this round."
            )
        mission_brief = render_mission_brief(supervised_config.context_packet_path)
        reviewer_background_context = "\n\n".join(
            part
            for part in (
                mission_brief,
                capsule_block,
                rotation_block,
                reviewer_background_context,
                *state.pending_secret_guard_notes,
                process_ownership_note,
            )
            if part
        )
        from ..reviewer._core import _parallel_final_review_passes

        preliminary_review = _parallel_final_review_passes(
            getattr(self.reviewer, "runner", self.reviewer),
            replace(
                self.reviewer_config,
                working_dir=str(workdir),
                artifact_root=str(workdir),
                narrative_snapshot_root=(
                    supervised_config.narrative_snapshot_root or None
                ),
            ),
        )
        if preliminary_review is not None:
            enforcement = supervised_config.narrative_review_enforcement
            if preliminary_review.backend_unavailable and enforcement == "blocking":
                return preliminary_review
            authority_note = (
                "Shadow calibration only: these new semantic-loss and cold-read signals "
                "cannot be the sole reason for a blocking verdict. Independently verify a "
                "finding under the existing scientific, visual, language, or venue contract "
                "before using it to continue the round."
                if enforcement != "blocking"
                else (
                    "Enforcement is enabled: a substantiated scientific loss or reject-level "
                    "cold-read failure may block certification."
                )
            )
            reviewer_background_context = "\n\n".join(
                part
                for part in (
                    reviewer_background_context,
                    "## Independent final-paper passes\n"
                    "These fresh read-only assessments are evidence for your integrated "
                    "verdict. Resolve conflicts yourself; only your verdict controls the "
                    "round and is persisted to paper/REVIEW.md. "
                    + authority_note
                    + "\n"
                    + preliminary_review.reason,
                )
                if part
            )
        started_at = time.monotonic()
        try:
            review = self.reviewer.evaluate(
                operation="evaluate",
                objective=objective,
                original_objective=original_objective or objective,
                operator_messages=operator_messages,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                session_id=supervised_config.session_id,
                main_summary=engineer_message or "(no message)",
                main_error=safe_fatal_error,
                config=replace(
                    self.reviewer_config,
                    working_dir=str(workdir),
                    artifact_root=str(workdir),
                ),
                prev_review_summary=_previous_review_summary(state),
                scope=scope,
                checkpoint_path=str(checkpoint_path or ""),
                background_context=reviewer_background_context,
                escalate_hint=escalate_hint,
                engineer_log_path=supervised_config.engineer_log_path,
                engineer_call_id=(
                    str(engineer_result.call_id or "")
                    if engineer_result.call_id_log_correlated
                    else ""
                ),
                preselected_skill_block=reviewer_skill_block,
                resume_thread_id=reviewer_resume_id,
                prior_static_fingerprint=(
                    reviewer_session.static_fingerprint if reviewer_session else ""
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if reviewer_session is not None:
                reviewer_session.rotate("backend_exception")
            msg = f"reviewer raised {type(exc).__name__}: {exc}"
            log.exception("reviewer raised during supervised round")
            return ReviewDecision(
                status="blocked",
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
            )
        session_metadata_persisted = True
        if reviewer_session is not None:
            if reviewer_resume_id and not review.session_resumed:
                reviewer_session.rotate("static_context_changed")
            session_metadata_persisted = reviewer_session.complete(
                review,
                decisive_output="\n".join(
                    part for part in (review.reason, review.next_action) if part
                ),
                static_fingerprint=review.static_fingerprint,
            )
            if review.backend_unavailable:
                reviewer_session.rotate("backend_failure")
        prompt_stats = review.prompt_block_stats or {}
        prompt_chars = int(
            (prompt_stats.get("delta_total") or {}).get("chars", 0) or 0
        )
        if not review.session_resumed:
            prompt_chars += int(
                (prompt_stats.get("static_total") or {}).get("chars", 0) or 0
            )
        if on_event and reviewer_session is not None:
            on_event({
                "type": EventType.ROLE_SESSION_TURN,
                "role": "reviewer",
                "policy": reviewer_session.policy,
                "action": reviewer_session.action,
                "rotation_reason": reviewer_session.rotation_reason,
                "round_index": round_index,
                "session_id": str(review.thread_id or ""),
                "turns_on_session": reviewer_session.turns,
                "input_tokens": int(review.input_tokens or 0),
                "cached_input_tokens": int(review.cached_input_tokens or 0),
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "prompt_chars": prompt_chars,
                "prompt_estimated_tokens": (prompt_chars + 3) // 4,
                "capsule_path": str(reviewer_session.path or ""),
                "metadata_persisted": session_metadata_persisted,
                "persistence_warning": reviewer_session.persistence_error,
                "operator_context_revision": operator_context_revision,
            })
        signal = review.session_signal if isinstance(review.session_signal, dict) else {}
        signal_kind = str(signal.get("kind") or "").strip()
        signal_target = str(signal.get("target") or "").strip()
        signal_detail = str(signal.get("detail") or "").strip()
        if signal_kind and signal_target:
            target_session = {
                "engineer": state.engineer_session,
                "reviewer": reviewer_session,
            }.get(signal_target)
            applied = False
            effective_policy = getattr(target_session, "policy", "fresh")
            if target_session is not None and target_session.policy != "fresh":
                target_session.signal(signal_kind, signal_detail)
                applied = True
            elif signal_target == "planner" and supervised_config.role_session_dir:
                # A planner capsule exists only for a resumable policy. Avoid
                # materialising rotation metadata for a fresh-only backend.
                effective_policy = supervised_config.role_session_policy
                from ..core.role_session import signal_role_session_file

                applied = signal_role_session_file(
                    supervised_config.role_session_dir / "planner.json",
                    signal_kind,
                    signal_detail,
                )
            if applied and on_event:
                on_event({
                    "type": EventType.ROLE_SESSION_TURN,
                    "role": signal_target,
                    "policy": effective_policy,
                    "action": "rotated",
                    "rotation_reason": f"signal:{signal_kind}",
                    "round_index": round_index,
                    "signal_kind": signal_kind,
                    "signal_detail": signal_detail,
                    "capsule_path": str(
                        getattr(target_session, "path", "")
                        or (
                            supervised_config.role_session_dir / f"{signal_target}.json"
                            if supervised_config.role_session_dir
                            else ""
                        )
                    ),
                })
        return review

    def _invoke_reviewer_with_retry(
        self,
        *,
        objective: str,
        original_objective: str | None,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        scope: str,
        checkpoint_path: Path | None,
        reviewer_skill_block: str | None,
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        prepare_review_context,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        engineer_message = outcome.engineer_message
        safe_fatal_error = outcome.safe_fatal_error
        process_ownership_note = outcome.process_ownership_note
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
        # State the harness rule before it can fire so the Reviewer knows that
        # an explicit true progress judgment preserves productive long work.
        escalate_hint = ""
        if (
            supervised_config.soft_round_limit
            and round_index >= supervised_config.soft_round_limit
        ):
            escalate_hint = (
                f"After round {supervised_config.soft_round_limit}, the harness "
                "settles the mission as stalled when neither of the last two "
                "Reviewer verdicts has `forward_progress=true`; genuine progress "
                "continues normally."
            )
            if on_event and round_index == supervised_config.soft_round_limit:
                on_event({
                    "type": EventType.ROUND_ESCALATED,
                    "round_index": round_index,
                    "soft_round_limit": supervised_config.soft_round_limit,
                    "hard_escalate_rounds": supervised_config.hard_escalate_rounds,
                    "text": (
                        f"round {round_index} reached soft limit "
                        f"{supervised_config.soft_round_limit}: reviewer told the "
                        "enforced two-verdict progress rule"
                    ),
                })
        # Evaluate the reviewer, retrying ONLY the reviewer on an infra flake.
        # The engineer's output for THIS round is already valid and in hand, so
        # a reviewer subprocess crash / 429 / missing-output-schema must retry
        # the (cheap) reviewer leg — NOT discard the round and re-run the
        # (xhigh) engineer turn. We leave this inner loop with a real verdict,
        # or by failing loud once the reviewer-backend streak hits threshold.
        while True:
            review = self._call_reviewer_once(
                objective=objective,
                original_objective=original_objective,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                scope=scope,
                checkpoint_path=checkpoint_path,
                reviewer_skill_block=reviewer_skill_block,
                escalate_hint=escalate_hint,
                engineer_result=engineer_result,
                engineer_message=engineer_message,
                safe_fatal_error=safe_fatal_error,
                process_ownership_note=process_ownership_note,
                state=state,
                on_event=on_event,
            )
            reviewer_fatal_error = str(review.backend_fatal_error or "")
            reviewer_exit_code = int(review.backend_exit_code or 0)
            reviewer_stop_kind = normalize_stop_kind(
                review.backend_stop_kind
            ) or stop_kind_from_external_interrupt(reviewer_fatal_error)
            reviewer_pause_status = pause_status_for_stop_kind(
                reviewer_stop_kind
            )
            if (
                review.backend_unavailable
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
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=reviewer_fatal_error,
                    stop_kind=reviewer_stop_kind,
                ))
                return control_return((
                    reviewer_pause_status,
                    state.rounds,
                    state.last_engineer_message,
                    review.reason,
                    None,
                ))
            if review.backend_unavailable and reviewer_stop_kind == "permanent_error":
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=reviewer_fatal_error,
                    stop_kind=reviewer_stop_kind,
                ))
                return control_return((
                    "error",
                    state.rounds,
                    state.last_engineer_message,
                    review.reason,
                    None,
                ))
            if (
                review.backend_unavailable
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
                    input_tokens=int(review.input_tokens or 0),
                    cached_input_tokens=int(review.cached_input_tokens or 0),
                    output_tokens=int(review.output_tokens or 0),
                    reasoning_output_tokens=int(review.reasoning_output_tokens or 0),
                    premium_requests=float(review.premium_requests or 0.0),
                )
                if on_event:
                    on_event(_review_event_payload(
                        interrupted_review,
                        round_index=round_index,
                        round_max=supervised_config.max_rounds,
                        text="review: skipped (operator abort requested)",
                        review_skipped=True,
                    ))
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=interrupted_review,
                    fatal_error=reviewer_fatal_error,
                    stop_kind="operator_abort",
                ))
                return control_return((
                    "aborted",
                    state.rounds,
                    state.last_engineer_message,
                    interrupted_review.reason,
                    None,
                ))
            if (
                review.backend_unavailable
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
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=interrupted_review,
                    fatal_error=reviewer_fatal_error,
                    stop_kind="daemon_shutdown",
                ))
                return control_return((
                    "paused_daemon_shutdown",
                    state.rounds,
                    state.last_engineer_message,
                    interrupted_review.reason,
                    None,
                ))
            # A Reviewer backend failure produces no verdict and must not become
            # a silent continuation. Use the same retry and escalation path as
            # Engineer backend failures. A genuine `blocked` verdict remains a
            # model decision and follows normal classification.
            if not review.backend_unavailable:
                break
            state.reviewer_backend_failure_streak += 1
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
                    "streak": state.reviewer_backend_failure_streak,
                    "threshold": rb_threshold,
                    "operator_alert": True,
                    "text": (
                        "reviewer backend unavailable "
                        f"{state.reviewer_backend_failure_streak}/{rb_threshold}: no "
                        "verdict rendered — NOT continuing blind. "
                        + review.reason
                    ),
                })
            if (
                state.reviewer_backend_failure_streak >= rb_threshold
                or (
                    supervised_config.max_rounds > 0
                    and round_index >= supervised_config.max_rounds
                )
            ):
                # Failing loud: record this round (with the in-hand engineer
                # output) and stop — do not run the completion gate blind.
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=engineer_result.fatal_error,
                ))
                return control_return((
                    "error",
                    state.rounds,
                    state.last_engineer_message,
                    (
                        "Reviewer backend unavailable for "
                        f"{state.reviewer_backend_failure_streak} consecutive "
                        "attempt(s); failing loud rather than running the "
                        "completion gate without a real review. "
                        + review.reason
                    ),
                    None,
                ))
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
        return control_proceed(review)
