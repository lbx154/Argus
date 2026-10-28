"""Reviewer-gated Engineer round orchestration."""
from __future__ import annotations

import itertools
import logging
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Callable

from ..core.handoff import HandOffActions
from ..core.mission_view import uses_cjk
from ..core.models import (
    LoopOutcome,
    LoopStatus,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from ..core.role_actions import role_actions
from ..core.role_session import (
    RoleSessionCapsule,
    objective_revision,
    role_session_policy,
)
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
)
from ..core.workspace_snapshot import WorkspaceSnapshots
from ..reviewer import Reviewer, ReviewerConfig
from .checkpoint import resolve_shared_checkpoint

log = logging.getLogger(__name__)
# Config dataclasses are re-exported here for historical/test imports.
from .round_config import (
    DEFAULT_LIVE_SEARCH_STAGES,
    EngineerConfig,
    SupervisedConfig,
    _engineer_live_search,
)
from .round_execution import RoundExecutionMixin
from .round_manager_wait import manager_wait_terminal
from .round_prompt import RoundPromptMixin
from .round_reviewer import RoundReviewerMixin
from .round_routing import (
    RoundRoutingMixin,
    accepted_as_is,
    accepted_verdict,
    climb_record,
    no_turn,
    outside_inputs,
    returned_unchanged,
)
from .round_settlement import RoundSettlementMixin

# These round-signal helpers remain module attributes because callers and tests
# patch them through ``argus.engineer.runner``.
from .round_signals import (
    _apply_round_secret_guard,  # noqa: F401
    _review_event_payload,  # noqa: F401
)
from .round_state import RoundControl, RoundLoopState, control_proceed, control_return
from .round_stop_signals import (
    backend_failure_review_decision,
    daemon_stop_review_decision,
    external_pause_review_decision,
    fatal_error_looks_like_backend_failure,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_model_configuration,
    fatal_error_looks_like_operator_abort_request,
    fatal_error_looks_like_recoverable_reconnect,
    model_configuration_review_decision,
    operator_abort_review_decision,
    runner_result_is_backend_failure,
    should_clear_thread_id_after_outcome,
)
from .turn_stall import LOOK_SECONDS, TurnStallWatch

# The next Reviewer reads at most this many earlier unreviewed Engineer
# accounts, each kept to its head and tail when longer than the char bound.
_UNREVIEWED_ACCOUNT_TURNS = 4
_UNREVIEWED_ACCOUNT_CHARS = 3000


def _carry_unreviewed_turn(state: RoundLoopState, round_index: int, message: str) -> None:
    """Keep the account of an Engineer turn that continued without review."""
    account = str(message or "").strip() or "(no message)"
    if len(account) > _UNREVIEWED_ACCOUNT_CHARS:
        omitted = len(account) - 2900
        account = (
            account[:2000]
            + f"\n…[middle omitted {omitted} characters]…\n"
            + account[-900:]
        )
    state.unreviewed_engineer_accounts.append(f"### Engineer round {round_index}\n{account}")
    del state.unreviewed_engineer_accounts[:-_UNREVIEWED_ACCOUNT_TURNS]


class SupervisedEngineer(
    RoundPromptMixin,
    RoundExecutionMixin,
    RoundRoutingMixin,
    RoundReviewerMixin,
    RoundSettlementMixin,
):
    """Run the Engineer with Reviewer-gated retries.

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
        review_completed_hook: Callable[[RoundRecord], None] | None = None,
        reviewer_skill_block: str | None = None,
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action, include_static)`` is called once
        per round. Round 1 receives the static task/Skill contract; continuation
        rounds default to a compact Reviewer delta plus CHECKPOINT.md. The
        backend-aware default resumes only supported native CLI roles inside
        this mission and persists a bounded role capsule; fresh remains the
        fallback for unsupported or isolated runners.

        Returns ``(status, rounds, final_message, reason, last_thread_id)``.

        This orchestrates the round loop's phase mixins in order — prompt
        assembly, engineer-turn execution, non-review stop shortcircuits,
        agent-driven background/external waits, progress
        bookkeeping, Reviewer invocation/retry, and round settlement — and
        interprets each phase's ``RoundControl`` verdict: ``return`` ends the
        mission with a terminal result, ``continue_loop`` immediately starts
        the next round, and falling through (``proceed``) lets the round
        continue to the next phase. The independent Reviewer owns completion.
        """
        if on_event is not None:
            raw_on_event = on_event

            def _redacted_on_event(event: dict) -> None:
                redacted = redact_secrets_record(
                    event,
                    known_values=known_secret_values(),
                )
                raw_on_event(redacted)

            on_event = _redacted_on_event
        state = RoundLoopState(chinese=uses_cjk(objective))
        from ..core.grounding_baseline import objective_baseline
        from .round_reviewer import (
            mission_closes,
            mission_item_id,
            mission_lineage,
            mission_state_root,
        )

        state.state_root = mission_state_root(supervised_config)
        state.mission_item_id = mission_item_id(supervised_config)
        state.closing = mission_closes(supervised_config, scope)
        state.mission_ref = str(getattr(supervised_config, "session_id", "") or "")
        # Before this mission's Engineer writes anything: what the workspace held
        # when the first mission on this objective began.
        try:
            state.grounding_baseline = objective_baseline(
                state.state_root, str(original_objective or objective or ""),
                (workdir, getattr(self.reviewer_config, "vertical_state_root", None)),
            )
        except Exception:  # noqa: BLE001 - without it, no workspace file grounds
            log.debug("grounding baseline unavailable", exc_info=True)
            state.grounding_baseline = None
        lineage = mission_lineage(supervised_config) or state.mission_ref
        if state.state_root is not None and lineage:
            snapshots = WorkspaceSnapshots(
                state.state_root / "snapshots.git", Path(workdir).resolve(), lineage,
            )
            # The task's baseline stays when the same work resumes or is replanned.
            state.snapshots = snapshots if snapshots.take("baseline", keep=True) else None
        if seed_thread_id:
            state.engineer_thread_ids.add(str(seed_thread_id))
        checkpoint_path = resolve_shared_checkpoint(supervised_config.checkpoint_path)
        capsule_dir = supervised_config.role_session_dir
        revision = objective_revision(objective)
        engineer_backend = str(
            getattr(self.engineer_runner, "backend", type(self.engineer_runner).__name__)
        )
        engineer_policy = role_session_policy(
            engineer_backend,
            allow_resume=supervised_config.engineer_operation != "narrative_edit",
        )
        state.engineer_session = RoleSessionCapsule.open(
            role="engineer",
            policy=engineer_policy,
            objective_revision=revision,
            workdir=workdir.resolve(),
            backend=engineer_backend,
            model=str(self.engineer_config.model or ""),
            checkpoint_path=checkpoint_path,
            path=(
                capsule_dir / "engineer.json"
                if capsule_dir and engineer_policy != "fresh"
                else None
            ),
            seed_thread_id=seed_thread_id,
            mission_context_path=supervised_config.context_packet_path,
        )
        reviewer_runner = getattr(self.reviewer, "runner", self.reviewer)
        reviewer_backend = str(
            getattr(reviewer_runner, "backend", type(reviewer_runner).__name__)
        )
        reviewer_policy = role_session_policy(reviewer_backend)
        state.reviewer_session = RoleSessionCapsule.open(
            role="reviewer",
            policy=reviewer_policy,
            objective_revision=revision,
            workdir=workdir.resolve(),
            backend=reviewer_backend,
            model=str(self.reviewer_config.model or ""),
            checkpoint_path=checkpoint_path,
            path=(
                capsule_dir / "reviewer.json"
                if capsule_dir and reviewer_policy != "fresh"
                else None
            ),
            mission_context_path=supervised_config.context_packet_path,
        )
        if supervised_config.engineer_operation == "narrative_edit":
            try:
                from ..core.manuscript_narrative_runtime import (
                    prepare_narrative_snapshot,
                )

                snapshot_root = prepare_narrative_snapshot(
                    workdir,
                    self.reviewer_config.vertical_state_root or workdir,
                    mission_id=(
                        supervised_config.narrative_mission_id
                        or supervised_config.session_id
                        or revision
                    ),
                )
                supervised_config = replace(
                    supervised_config,
                    narrative_snapshot_root=str(snapshot_root),
                )
            except Exception as exc:  # noqa: BLE001 - comparison is a hard contract
                reason = f"Could not preserve the pre-edit paper snapshot: {exc}"
                return "error", state.rounds, "", reason, None
        # A climb is said in the language of the task: the person reads it.
        chinese = uses_cjk(objective)
        round_indices = (
            itertools.count(1)
            if supervised_config.max_rounds <= 0
            else range(1, supervised_config.max_rounds + 1)
        )
        for round_index in round_indices:
            paused = manager_wait_terminal(supervised_config, state)
            if paused is not None:
                return paused
            turn = state.snapshots is not None and state.snapshots.take("turn")
            state.handed_inputs = outside_inputs(state) if turn else None
            climbed = turn and not state.review_unseen and (
                returned_unchanged("engineer", state, "turn") or accepted_as_is(state, "turn")
            )
            if climbed:
                # The Engineer already had the work as it is now, or the Reviewer
                # accepted it as it is, and nothing new arrived: no turn.
                outcome = no_turn(state)
            else:
                control = self._take_engineer_turn(
                    round_index=round_index,
                    supervised_config=supervised_config,
                    engineer_prompt_builder=engineer_prompt_builder,
                    checkpoint_path=checkpoint_path,
                    workdir=workdir,
                    state=state,
                    on_event=on_event,
                )
                if control.action == "return":
                    return control.terminal
                if control.action == "continue_loop":
                    continue
                outcome = control.payload

            control = self._route_turn(
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                on_event=on_event,
                chinese=chinese,
                climbed=climbed,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                _carry_unreviewed_turn(state, round_index, outcome.engineer_message)
                continue

            state.handed_inputs = outside_inputs(state)
            control = self._invoke_reviewer_with_retry(
                objective=objective,
                original_objective=original_objective,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                scope=scope,
                checkpoint_path=checkpoint_path,
                reviewer_skill_block=reviewer_skill_block,
                outcome=outcome,
                state=state,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue
            review = control.payload
            if state.snapshots is not None and state.snapshots.take("reviewed"):
                # The Reviewer holds the work as its verdict leaves it.
                state.snapshots.hold(
                    "reviewer", "reviewed", state.handed_inputs, accepted_verdict(review, round_index, state),
                )
                if review.status in {"planner", "manager"}:
                    holder, passed = self._deliver(review.status, state=state, label="reviewed")
                    review = climb_record(holder, chinese=chinese, handed=review) if passed else review
            control = self._settle_round(
                review=review,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                on_event=on_event,
            )
            state.review_unseen = bool(state.reviewer_next_action)
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue
            # else "proceed": fall through to the next round.

        # Only an explicit positive max_rounds can exhaust the finite range.
        return (
            "max_rounds",
            state.rounds,
            state.last_engineer_message,
            f"Hit max_rounds={supervised_config.max_rounds} without "
            "reviewer-confirmed completion.",
            None,
        )

    def _take_engineer_turn(
        self,
        *,
        round_index: int,
        supervised_config: SupervisedConfig,
        engineer_prompt_builder: Callable[[str | None, bool], str],
        checkpoint_path: Path | None,
        workdir: Path,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        """Run one Engineer turn; ``proceed`` carries its outcome to routing."""
        engineer_resume_id = state.engineer_session.prepare()
        engineer_prompt = self._assemble_round_prompt(
            round_index=round_index,
            supervised_config=supervised_config,
            engineer_prompt_builder=engineer_prompt_builder,
            reviewer_next_action=state.reviewer_next_action,
            checkpoint_path=checkpoint_path,
            workdir=workdir,
            role_session=state.engineer_session,
            on_event=on_event,
        )
        if state.pending_engineer_followup:
            engineer_prompt += "\n\n" + state.pending_engineer_followup
            state.pending_engineer_followup = ""

        outcome = self._run_engineer_turn(
            round_index=round_index,
            engineer_prompt=engineer_prompt,
            workdir=workdir,
            supervised_config=supervised_config,
            checkpoint_path=checkpoint_path,
            resume_thread_id=engineer_resume_id,
            on_event=on_event,
            state=state,
        )

        control = self._handle_stop_kind_shortcircuit(
            round_index=round_index,
            supervised_config=supervised_config,
            outcome=outcome,
            state=state,
            on_event=on_event,
        )
        if control.action != "proceed":
            return control
        paused = manager_wait_terminal(supervised_config, state)
        return control_return(paused) if paused is not None else control_proceed(outcome)

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
        actions: HandOffActions | None = None,
    ) -> tuple[RunnerResult, int]:
        """One Engineer call; with ``actions``, the turn ends with that form's ``hand_off``."""
        stalled_idle_seconds: int | None = None
        stall_watch: TurnStallWatch | None = None
        if supervised_config is not None:
            stalled_idle_seconds = int(supervised_config.runner_stalled_idle_seconds or 0)
            if stalled_idle_seconds > 0:
                stall_watch = TurnStallWatch(
                    stall_seconds=stalled_idle_seconds,
                    state_root=supervised_config.state_root,
                    run_label=run_label,
                    on_event=on_event,
                )
        packet = supervised_config.context_packet_path if supervised_config is not None else ""
        try:
            options = RunnerOptions(
                model=self.engineer_config.model,
                reasoning_effort=(
                    reasoning_effort
                    if reasoning_effort is not None
                    else self.engineer_config.reasoning_effort
                ),
                extra_args=self.engineer_config.extra_args,
                add_dirs=self.engineer_config.add_dirs,
                # It edits CHECKPOINT.md beside its handoff packet.
                write_dirs=[str(Path(packet).expanduser().resolve().parent)] if packet else None,
                skill_paths=self.engineer_config.skill_paths,
                full_auto=self.engineer_config.full_auto,
                skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                dangerous_yolo=self.engineer_config.dangerous_yolo,
                sandbox_mode=self.engineer_config.sandbox_mode,
                isolate_workdir=self.engineer_config.isolate_workdir,
                # The Engineer keeps its state in CHECKPOINT.md, so the
                # agent CLI's own tools it never uses stay out of requests.
                lean_tool_surface=True,
                working_dir=str(workdir),
                live_search=_engineer_live_search(
                    self.engineer_config.vertical_state_root or workdir,
                    self.engineer_config.live_search_stages,
                ),
                # Provider/process liveness belongs to the backend's stream
                # watchdog. Do not infer semantic progress from project
                # mtimes or provider-private session files.
                external_interrupt_reason_provider=None,
                inactivity_callback=stall_watch,
                watchdog_soft_idle_seconds=LOOK_SECONDS if stall_watch else None,
                watchdog_stalled_idle_seconds=stalled_idle_seconds,
                extension_env=(
                    {
                        "ARGUS_PLUGIN_PARENT_MISSION_ID": str(
                            supervised_config.session_id
                        )
                    }
                    if supervised_config is not None
                    and supervised_config.session_id
                    else None
                ),
            )
            with role_actions(self.engineer_runner, options, actions) if actions else nullcontext(options) as options:
                result = gateway_run_exec(
                    self.engineer_runner, prompt=prompt, options=options,
                    run_label=run_label, resume_thread_id=resume_thread_id,
                )
            return result, 0
        except Exception as exc:  # noqa: BLE001
            msg = f"engineer runner raised {type(exc).__name__}: {exc}"
            log.exception("engineer runner raised during %s", run_label)
            return RunnerResult(
                exit_code=-1,
                fatal_error=msg,
                stderr_lines=[msg],
                stop_kind="backend_unavailable",
            ), 0


__all__ = [
    "DEFAULT_LIVE_SEARCH_STAGES",
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
    "fatal_error_looks_like_recoverable_reconnect",
    "should_clear_thread_id_after_outcome",
]
