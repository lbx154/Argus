"""Round-loop phase: Reviewer invocation and infra-only retry.

Owns calling the independent Reviewer for the current round and retrying
ONLY the reviewer leg on an infra flake (subprocess crash, missing verdict
schema, a reviewer that wrote directly to a protected wiki card instead of
using structured ``wiki_ops``) — never discarding the Engineer's already-valid
output and never re-running the (expensive) Engineer turn just because the
cheap Reviewer call hiccuped. Reviewer backend death must never be laundered
into a silent ``continue``: it is routed through the same transient-backoff +
escalate-to-error machinery the Engineer backend-failure path uses, so the
harness can never run the sole completion gate blind. Once a real (non
backend-failure) verdict is obtained, this phase hands the ``ReviewDecision``
to the round-settlement phase via ``RoundControl.payload``.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
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
        """Call the Reviewer once, retrying only on a direct wiki-write
        violation (up to 3 attempts) — a protected wiki card write must go
        through structured ``wiki_ops``, so a caught direct edit is atomically
        reverted and the same reviewer turn re-run rather than accepted.
        """
        reviewer_direct_write_violations = 0
        while True:
            wiki_pages_before_review = _snapshot_wiki_pages(workdir)
            reviewer_background_context = ""
            if supervised_config.background_subagent_advisory:
                try:
                    reviewer_background_context = render_external_work_advisory(
                        workdir,
                        include_subagents=True,
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
                    round_max=supervised_config.max_rounds,
                    session_id=supervised_config.session_id,
                    main_summary=(
                        "\n\n".join(
                            part
                            for part in (
                                engineer_message or "(no message)",
                                *state.pending_secret_guard_notes,
                                process_ownership_note,
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
                if violation not in state.pending_secret_guard_notes:
                    state.pending_secret_guard_notes.append(violation)
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
                    backend_unavailable=True,
                    backend_stop_kind="transient_error",
                    backend_fatal_error=violation,
                )
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
            reviewer_fatal_error = str(
                getattr(review, "backend_fatal_error", "") or "")
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
            if (
                getattr(review, "backend_unavailable", False)
                and reviewer_stop_kind == "permanent_error"
            ):
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
                or round_index >= supervised_config.max_rounds
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
