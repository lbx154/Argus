"""Round-loop phase: Reviewer invocation and infra-only retry.

Owns calling the independent Reviewer for the current round and retrying
ONLY the reviewer leg on a failed call or an unsubmitted review action —
never discarding the Engineer's already-valid output and never
re-running the (expensive) Engineer turn just because the cheap Reviewer call
hiccuped. Reviewer backend death must never be laundered
into a silent ``continue``: it is routed through the same transient-backoff +
escalate-to-error machinery the Engineer backend-failure path uses, so the
harness can never run the sole completion gate blind. Once a real (non
backend-failure) verdict is obtained, this phase hands the ``ReviewDecision``
to the round-settlement phase via ``RoundControl.payload``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..core.event_catalog import EventType
from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from ..core.operator_messages import uses_cjk
from ..core.runner_errors import is_execution_host_startup_error
from ..core.stop_kinds import (
    normalize_stop_kind,
    stop_kind_from_external_interrupt,
    stop_park_reason,
)
from ..life.context_packet import render_mission_brief
from ..life.memory import Backlog
from .reviewer_findings import (
    own_reviewer_thread,
    render_previous_findings,
    reviewer_authored_rounds,
    reviewer_words_of,
)
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
    fatal_error_looks_like_provider_turn_cap,
    operator_abort_review_decision,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


def mission_state_root(supervised_config: Any) -> Path | None:
    """The project state directory the mission belongs to, if it has one."""
    root = getattr(supervised_config, "state_root", None)
    if root:
        return Path(root)
    packet = str(getattr(supervised_config, "context_packet_path", "") or "").strip()
    if packet:
        parents = Path(packet).expanduser().parents
        if len(parents) >= 3:
            return parents[2]
    return None


def _operator_questions_allowed(supervised_config: "SupervisedConfig") -> bool:
    from ..core.autonomy import operator_questions_can_be_asked

    root = getattr(supervised_config, "state_root", None)
    if root is not None:
        return operator_questions_can_be_asked(root)
    return (
        bool(getattr(supervised_config, "operator_questions_allowed", True))
        and operator_questions_can_be_asked()
    )


def _mission_packet(supervised_config: Any) -> dict[str, Any]:
    """This mission's context packet, or {}."""
    packet = str(getattr(supervised_config, "context_packet_path", "") or "").strip()
    if not packet:
        return {}
    try:
        payload = json.loads(Path(packet).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def mission_item_id(supervised_config: Any) -> str:
    """The backlog item this mission works on, from its context packet, or ""."""
    return str(_mission_packet(supervised_config).get("mission_id") or "").strip()


def mission_lineage(supervised_config: Any) -> str:
    """The work this mission continues: its ``node_key``, else its item, or ""."""
    packet = _mission_packet(supervised_config)
    return str(packet.get("node_key") or "").strip() or str(packet.get("mission_id") or "").strip()


def mission_contract(supervised_config: Any) -> dict[str, Any]:
    """The mission contract (``mission.json``) the packet belongs to, or {}."""
    packet = str(getattr(supervised_config, "context_packet_path", "") or "").strip()
    if not packet:
        return {}
    path = Path(packet).expanduser()
    for _ in range(2):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if payload.get("kind") == "mission_context":
            return payload
        mission = payload.get("mission")
        reference = str(mission.get("path") or "") if isinstance(mission, dict) else ""
        if not reference:
            return {}
        path = Path(reference).expanduser()
    return {}


def _no_reviewer_judgment(review: ReviewDecision) -> ReviewDecision:
    """The record a round keeps when the Reviewer leg rendered no verdict.

    The decision stays as the backend reported it; only its provenance now
    says the host wrote it, so the mission journal never credits this
    ``blocked`` to the Reviewer and the Manager's stage writer is not asked to
    act on it. The ``round.review.completed`` event already went out with the
    Reviewer as the unreachable role.
    """
    return replace(
        review,
        review_source="reviewer_unavailable",
        host_placeholder="reviewer_unavailable",
    )


def _previous_review_summary(state: RoundLoopState) -> str:
    """Render the last three Reviewer verdicts so repetition is visible to it.

    Only judgments the independent Reviewer returned count: host placeholders
    and self-reviews must never read as settled Reviewer context.
    """
    reviewed = reviewer_authored_rounds(state.rounds)
    if not reviewed:
        return ""
    lines: list[str] = []
    for record in reviewed[-3:]:
        own_status, own_reason, _ = reviewer_words_of(record.review)
        status = " ".join(own_status.split()) or "unknown"
        reason = " ".join(own_reason.split()) or "(no reason)"
        lines.append(
            f"Round {record.round_index} — {status}: {reason[:600]}"
        )
    return "\n".join(lines)


def _unreviewed_turns_block(state: RoundLoopState) -> str:
    """Show the Reviewer the Engineer turns no review has judged yet.

    A turn that handed the work to the Engineer again, or waited on background
    work, ended without review, and the round summary is only the last turn's
    account. A change made in an earlier turn (switching the evaluation split,
    say) reached the Reviewer only if that last account happened to mention it.
    """
    if not state.unreviewed_engineer_accounts:
        return ""
    return (
        "## Earlier Engineer turns since the last review\n"
        "These turns continued without review. Their work is part of what you "
        "are judging, and the modified-files list covers them too.\n\n"
        + "\n\n".join(state.unreviewed_engineer_accounts)
    )


def _active_manager_directive_for_reviewer(
    supervised_config: "SupervisedConfig",
) -> list[str]:
    """Load the Reviewer-trimmed projection from the one operator store."""
    candidates: list[Path] = []
    if supervised_config.state_root is not None:
        # An explicit source owns even an empty/revoked projection. The log
        # and packet paths only locate context for older callers without it.
        candidates.append(Path(supervised_config.state_root))
    else:
        if supervised_config.engineer_log_path:
            candidates.append(Path(supervised_config.engineer_log_path).expanduser().parent)
        if supervised_config.context_packet_path:
            packet = Path(supervised_config.context_packet_path).expanduser()
            if len(packet.parents) >= 3:
                candidates.append(packet.parents[2])
    from ..core.file_lock import FileLockCancelled, bounded_file_lock_wait
    from ..core.operator_context import OperatorContextUnavailable, build_operator_context_block
    from ..core.run_gateway import current_run_interrupt_reason

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            root = candidate.resolve()
        except OSError:
            root = candidate
        if root in seen:
            continue
        seen.add(root)
        try:
            # Only prompt preparation inherits cancellation. Once acquired,
            # projection/once consumption remains atomic, and settlement has
            # no cancelled lock budget to inherit.
            with bounded_file_lock_wait(
                timeout_seconds=float("inf"),
                cancelled=lambda: bool(current_run_interrupt_reason()),
            ):
                message, _revision = build_operator_context_block(
                    "reviewer", root, mission_id=str(supervised_config.session_id or ""),
                )
        except FileLockCancelled as exc:
            if current_run_interrupt_reason():
                # The enclosing run retains the reason; the gateway produces
                # its ordinary stopped result before invoking any backend.
                return []
            raise OperatorContextUnavailable("Current Reviewer OperatorContext read was cancelled") from exc
        except Exception as exc:
            raise OperatorContextUnavailable("Current Reviewer OperatorContext is unavailable") from exc
        # Acceptances of an impossible check, scoped to this item or to a
        # named check; never to the whole objective.
        from ..core.residual_risk import reviewer_block

        try:
            accepted = reviewer_block(
                root, item_id=mission_item_id(supervised_config),
                mission_ref=str(getattr(supervised_config, "session_id", "") or ""),
            )
        except Exception:  # noqa: BLE001 - an unreadable record accepts nothing
            accepted = ""
        if message or accepted:
            return [text for text in (message, accepted) if text]
    return []


def backlog_history(root: Path | None) -> list[Any] | None:
    """Every backlog row under ``root``: none when it has no backlog, ``None`` when it cannot be read."""
    if root is None or not (Path(root) / "backlog.jsonl").is_file():
        return []
    try:
        return Backlog(Path(root) / "backlog.jsonl").history()
    except Exception:  # noqa: BLE001 - the caller decides what unreadable means
        return None


def operator_risk_decisions(root: Path | None, item_id: str) -> list[dict[str, Any]]:
    """Resolved operator decision cards on this item and the items it continues.

    An answer resolves the card on the task it parked, which resumes; an older
    release continued the work as a new item, whose answering card sits on the
    item it continues.
    """
    # An unreadable backlog accepts nothing.
    items = backlog_history(root) if item_id else []
    if not items:
        return []
    continued = {
        str((item.operator_decision or {}).get("continuation_item_id") or ""): item for item in items
        if isinstance(item.operator_decision, dict)
    }
    by_id = {item.id: item for item in items}
    cards: list[dict[str, Any]] = []
    current, seen = item_id, set()
    while current and current not in seen:
        seen.add(current)
        own = by_id.get(current)
        if own is not None and isinstance(own.operator_decision, dict) and own.operator_decision:
            cards.append(dict(own.operator_decision))
        previous = continued.get(current)
        current = previous.id if previous is not None else ""
    return [card for card in cards if card.get("status") == "resolved"]


def _recorded_facts(value: object) -> list[int] | None:
    """The ``file_facts`` the Planner recorded as JSON; an older digest records none."""
    try:
        facts = json.loads(str(value))
    except ValueError:
        return None
    return facts if isinstance(facts, list) else None


def mission_grounding(
    supervised_config: Any, *, item_id: str, mission_ref: str, baseline: dict[str, list[int]] | None,
) -> dict[str, Any]:
    """What this mission's Reviewer quotes and acceptances are checked against.

    ``baseline``: the workspace's files as recorded when the first mission on
    this objective began; a file that no longer matches (or was not there) is
    never a source. ``packet_refs``: files the task packet names, each with the
    ``file_facts`` the Planner recorded, which are sources while they still match.
    ``accepted_risks``: the Manager's acceptances in force for this item;
    ``operator_decisions``: the operator's resolved decision cards for it.
    ``operator_questions_allowed``: whether a question reaches the operator in
    this mission; when it cannot, accepting a risk is the Manager's.
    """
    from ..core.residual_risk import active_residual_risks

    contract = mission_contract(supervised_config)
    refs = tuple(
        {
            "ref": str(ref.get("ref") or "").strip(),
            "content_hash": _recorded_facts(ref.get("content_hash")),
        }
        for ref in contract.get("context_refs") or []
        if isinstance(ref, dict) and str(ref.get("ref") or "").strip()
    )
    root = mission_state_root(supervised_config)
    try:
        accepted = active_residual_risks(root, item_id=item_id, mission_ref=mission_ref) if root else []
    except Exception:  # noqa: BLE001 - an unreadable record accepts nothing
        accepted = []
    return {
        "baseline": baseline, "packet_refs": refs, "accepted_risks": tuple(accepted),
        "operator_decisions": tuple(operator_risk_decisions(root, item_id)),
        # The same rule settlement applies to this Reviewer's questions, so
        # the acceptance it is told to seek is one that can actually be given.
        "operator_questions_allowed": _operator_questions_allowed(supervised_config),
    }


def _mission_grounding(supervised_config: Any, state: RoundLoopState) -> dict[str, Any] | None:
    """The objective's workspace baseline, packet files and recorded acceptances, for the Reviewer's tools."""
    try:
        return mission_grounding(
            supervised_config, item_id=state.mission_item_id, mission_ref=state.mission_ref,
            baseline=state.grounding_baseline,
        )
    except Exception:  # noqa: BLE001 - without it, only the task text grounds and nothing is accepted
        log.debug("reviewer mission grounding unavailable", exc_info=True)
        return None


class RoundReviewerMixin:
    """Mixin providing ``SupervisedEngineer``'s reviewer-invocation phase."""

    if TYPE_CHECKING:
        # Supplied by RoundExecutionMixin on the composed SupervisedEngineer.
        def _hold_before_backend_failure_retry(
            self, seconds: float, *, retry_runner: object | None = None,
        ) -> str | None: ...

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

        reviewer_session = state.reviewer_session
        reviewer_resume_id = (
            reviewer_session.prepare()
            if reviewer_session is not None
            else None
        )
        engineer_session = state.engineer_session
        own_resume_id = own_reviewer_thread(
            reviewer_resume_id,
            foreign_thread_ids=(
                *state.engineer_thread_ids,
                getattr(engineer_session, "thread_id", None),
                *getattr(engineer_session, "seen_thread_ids", ()),
                getattr(engineer_result, "thread_id", None),
            ),
        )
        if reviewer_resume_id and own_resume_id is None and reviewer_session is not None:
            # Never continue the Engineer's conversation: start a fresh
            # Reviewer thread; its findings arrive through the carry-over.
            reviewer_session.rotate("foreign_thread")
        reviewer_resume_id = own_resume_id
        previous_findings = render_previous_findings(
            state.rounds, round_index=round_index,
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
                "was rotated. Do not reenact an earlier Engineer stage, recreate the "
                "files it produced, or ask for an approval already recorded in the "
                "canonical checkpoint. Read the current Engineer summary and the "
                "files it points to, then give your judgment on this round."
            )
        mission_brief = render_mission_brief(
            supervised_config.context_packet_path,
            include_engineer_account=False,
            # The carry-over is the one copy of this Reviewer's own findings.
            include_previous_review=not previous_findings.has_reviewer_findings,
        )
        shared_context_parts = (
            mission_brief,
            _unreviewed_turns_block(state),
            capsule_block,
            rotation_block,
            *state.pending_secret_guard_notes,
            process_ownership_note,
        )
        reviewer_background_context = "\n\n".join(
            part for part in shared_context_parts if part
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
                review_policy_context="\n".join(
                    (objective, original_objective or objective, scope, *operator_messages)
                ),
            ),
            current_work="\n\n".join(
                part for part in (
                    "## Engineer's account of this round\n"
                    + (engineer_message or "(no message)"),
                    reviewer_background_context,
                    _previous_review_summary(state),
                ) if part
            ),
        )
        if preliminary_review is not None:
            enforcement = supervised_config.narrative_review_enforcement
            if preliminary_review.backend_unavailable and enforcement == "blocking":
                return preliminary_review
            # Specialist passes can take minutes. The integrated Reviewer must
            # receive instructions that changed during that work.
            operator_messages = _active_manager_directive_for_reviewer(supervised_config)
            authority_note = (
                "Shadow calibration only: these new semantic-loss and cold-read signals "
                "cannot be the sole reason to rule that the work does not hold. "
                "Independently confirm a finding against the existing scientific, "
                "visual, language, or venue standards before using it to continue "
                "the round."
                if enforcement != "blocking"
                else (
                    "Enforcement is enabled: a substantiated scientific loss, or a "
                    "cold-read failure a venue reviewer would reject the paper over, "
                    "can be the reason the work does not hold yet."
                )
            )
            reviewer_background_context = "\n\n".join(
                part
                for part in (
                    reviewer_background_context,
                    "## Independent final-paper passes\n"
                    "These current host-provided read-only assessments are evidence for "
                    "your own integrated judgment. Resolve conflicts yourself; only your "
                    "judgment settles the round and is persisted to paper/REVIEW.md. The "
                    "host reuses PDF-only assessments only when their exact rendered "
                    "input and policy match. Do not launch duplicate specialist passes "
                    "or repeat a complete PDF inspection; use targeted checks for a "
                    "concrete contradiction. Always independently check material "
                    "changes to code, raw evidence, and claims. Apply any newer "
                    "operator directive that arrived during these passes and "
                    "independently check the findings it affects. "
                    + authority_note
                    + "\n"
                    + preliminary_review.reason,
                )
                if part
            )
        operator_context_revision = operator_context_revision_from_text(
            "\n".join(operator_messages)
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
                    snapshots=(
                        state.snapshots
                        if state.snapshots and state.snapshots.take("candidate")
                        else None
                    ),
                    mission_grounding=_mission_grounding(supervised_config, state),
                ),
                prev_review_summary=_previous_review_summary(state),
                # Host-gathered round evidence (e.g. the research round log):
                # facts the Reviewer weighs, rendered as raw evidence.
                raw_evidence="\n\n".join(state.pending_round_evidence),
                scope=scope,
                checkpoint_path=str(checkpoint_path or ""),
                background_context=reviewer_background_context,
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
                previous_findings=previous_findings.text,
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
        if not review.backend_unavailable:
            # A completed review has seen the host-gathered round evidence and
            # every Engineer turn since the last review; a backend failure keeps
            # them for the retry. The next window opens with the next turn.
            state.pending_round_evidence.clear()
            state.unreviewed_engineer_accounts.clear()
            # Provenance: only this call's own judgment becomes a finding
            # carried forward to later rounds.
            review.reviewer_authored = True
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
                **({"ends_work": True} if review.status == "done" else {}),
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
            capsule_path = getattr(target_session, "path", None)
            if target_session is not None and target_session.policy != "fresh":
                target_session.signal(signal_kind, signal_detail)
                applied = True
            elif signal_target == "planner" and supervised_config.context_packet_path:
                # The Planner keeps one project-level capsule beside
                # ``handoffs/`` (``<state>/handoffs/<mission>/mission.json``),
                # not one per mission. It exists only for a resumable policy;
                # the signal never creates one, so a missing file means there
                # is no Planner thread to rotate.
                effective_policy = "rolling"
                from ..core.role_session import signal_role_session_file

                capsule_path = (
                    Path(supervised_config.context_packet_path).expanduser().parents[2]
                    / "role-sessions" / "planner.json"
                )
                applied = signal_role_session_file(
                    capsule_path,
                    signal_kind,
                    signal_detail,
                )
                if not applied:
                    log.info(
                        "Reviewer signal %s for the Planner found no capsule at %s",
                        signal_kind, capsule_path,
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
                    "capsule_path": str(capsule_path or ""),
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
        # Evaluate the reviewer, retrying ONLY the reviewer on an infra flake.
        # The engineer's output for THIS round is already valid and in hand, so
        # a reviewer subprocess crash / 429 / missing-output-schema must retry
        # the (cheap) reviewer leg — NOT discard the round and re-run the
        # (xhigh) engineer turn. We leave this inner loop with a real verdict,
        # or by failing loud once the reviewer-backend streak hits threshold.
        reviewer_turn_cap_restarts = 0
        while True:
            from .round_manager_wait import manager_wait_terminal

            paused = manager_wait_terminal(supervised_config, state)
            if paused is not None:
                return control_return(paused)
            review = self._call_reviewer_once(
                objective=objective,
                original_objective=original_objective,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                scope=scope,
                checkpoint_path=checkpoint_path,
                reviewer_skill_block=reviewer_skill_block,
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
            if (
                review.backend_unavailable
                and is_execution_host_startup_error(reviewer_fatal_error)
            ):
                # Retrying the reviewer leg cannot install its missing host.
                # Keep the Engineer's output and park the same mission through
                # the same persistent gate used for Engineer startup failures.
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=review,
                    fatal_error=reviewer_fatal_error,
                    stop_kind="backend_unavailable",
                ))
                return control_return((
                    "parked",
                    state.rounds,
                    state.last_engineer_message,
                    reviewer_fatal_error,
                    None,
                ))
            if review.backend_unavailable and stop_park_reason(reviewer_stop_kind):
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
                    "parked",
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
                    review=_no_reviewer_judgment(review),
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
                    "parked",
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
            if (
                fatal_error_looks_like_provider_turn_cap(reviewer_fatal_error)
                and reviewer_turn_cap_restarts < 1
            ):
                # The Reviewer call ended at its per-call provider-turn
                # allowance — housekeeping, not a backend failure. Its inputs
                # (the Engineer summary, the checkpoint, the files on disk) are
                # all still on hand, so retry the review once in a fresh
                # session without touching the failure streak. A second capped
                # attempt falls through to the ordinary failure accounting so
                # a review that simply cannot finish still fails loud.
                reviewer_turn_cap_restarts += 1
                if on_event:
                    on_event({
                        "type": "round.provider_turn_cap.reviewer_restart",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "text": (
                            f"Round {round_index}: the Reviewer's session used "
                            "its whole per-call provider-turn allowance — a "
                            "routine pause, not an error. The review of the "
                            "same Engineer round restarts in a fresh session."
                        ),
                    })
                continue
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
                        "judgment was rendered — not continuing blind. "
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
                # The stop kind tells the host the model service failed, so a
                # run of such tasks is bounded like any automatic retry.
                state.rounds.append(RoundRecord(
                    round_index=round_index,
                    engineer_message=engineer_message,
                    engineer_exit_code=engineer_result.exit_code,
                    review=_no_reviewer_judgment(review),
                    fatal_error=engineer_result.fatal_error,
                    stop_kind=reviewer_stop_kind or "backend_unavailable",
                ))
                # Said in the language of the task, since this sentence is
                # what the operator reads when the task stops.
                attempts = state.reviewer_backend_failure_streak
                stopped_because = (
                    f"审阅者连续 {attempts} 次没能给出判断，Argus 选择停下这项"
                    "任务，而不是在没有真正审阅的情况下结束这一轮。"
                    if uses_cjk(objective)
                    else (
                        f"The Reviewer could not reach a judgment {attempts} "
                        f"time{'s' if attempts != 1 else ''} in a row, so Argus "
                        "stopped this task rather than settle the round "
                        "without a real judgment. "
                    )
                )
                return control_return((
                    "error",
                    state.rounds,
                    state.last_engineer_message,
                    stopped_because + review.reason,
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
                interrupt_reason = self._hold_before_backend_failure_retry(
                    backoff_seconds, retry_runner=getattr(getattr(self, "reviewer", None), "runner", None),
                )
                if interrupt_reason:
                    interrupt_kind = stop_kind_from_external_interrupt(interrupt_reason)
                    status: LoopStatus = "aborted" if interrupt_kind == "operator_abort" else "parked"
                    if status == "parked":
                        interrupt_kind = stop_park_reason(interrupt_kind) or "operator_pause"
                    reason_text = (
                        "The wait after a Reviewer backend failure ended "
                        f"early: {interrupt_reason}."
                    )
                    # The Engineer completed this round, but no Reviewer
                    # judgment arrived. Preserve both facts without another call.
                    state.rounds.append(RoundRecord(
                        round_index=round_index,
                        engineer_message=engineer_message,
                        engineer_exit_code=engineer_result.exit_code,
                        review=_no_reviewer_judgment(review),
                        fatal_error=f"External interrupt: {interrupt_reason}",
                        stop_kind=interrupt_kind,
                    ))
                    if on_event:
                        on_event({
                            "type": "round.backend_failure.hold_interrupted",
                            "round_index": round_index,
                            "round_max": supervised_config.max_rounds,
                            "stop_kind": interrupt_kind,
                            "text": reason_text,
                        })
                    return control_return((
                        status, state.rounds, state.last_engineer_message, reason_text, None,
                    ))
            # Retry ONLY the reviewer against the SAME engineer output — do
            # not fall through to a fresh (xhigh) engineer turn.
            continue
        # A real reviewer verdict arrived — reset the reviewer-backend streak.
        return control_proceed(review)
