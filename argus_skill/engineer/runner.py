"""Reviewer-gated Engineer round orchestration."""
from __future__ import annotations

import itertools
import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable

from ..core.models import (
    LoopOutcome,
    LoopStatus,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from ..core.role_session import (
    RoleSessionCapsule,
    effective_role_session_policy,
    objective_revision,
)
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
)
from ..reviewer import Reviewer, ReviewerConfig
from .checkpoint import resolve_shared_checkpoint

log = logging.getLogger(__name__)
# Config dataclasses are re-exported here for historical/test imports.
from .round_config import (
    DEFAULT_LIVE_SEARCH_STAGES,
    EngineerConfig,
    SupervisedConfig,
    _engineer_live_search,
    parse_continue_work_request,
)
from .round_execution import RoundExecutionMixin
from .round_prompt import RoundPromptMixin
from .round_reviewer import RoundReviewerMixin
from .round_self_review import RoundSelfReviewMixin
from .round_settlement import (
    RoundSettlementMixin,
    enforce_question_policy_event,
    enforce_terminal_question_policy,
)

# These round-signal helpers remain module attributes because callers and tests
# patch them through ``argus_skill.engineer.runner``.
from .round_signals import (
    _apply_round_secret_guard,  # noqa: F401
    _pause_decision_clock,  # noqa: F401
    _review_event_payload,  # noqa: F401
    _run_external_work_wait,  # noqa: F401
)
from .round_state import RoundLoopState
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
from .round_waits import RoundWaitsMixin


class SupervisedEngineer(
    RoundPromptMixin,
    RoundExecutionMixin,
    RoundWaitsMixin,
    RoundSelfReviewMixin,
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
        prepare_review_context: Callable[[], None] | None = None,
        review_completed_hook: Callable[[RoundRecord], None] | None = None,
        continue_adaptor: Callable[[list[RoundRecord]], str] | None = None,
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
                raw_on_event(
                    enforce_question_policy_event(redacted, supervised_config)
                )

            on_event = _redacted_on_event
        state = RoundLoopState()
        checkpoint_path = resolve_shared_checkpoint(supervised_config.checkpoint_path)
        capsule_dir = supervised_config.role_session_dir
        revision = objective_revision(objective)
        engineer_backend = str(
            getattr(self.engineer_runner, "backend", type(self.engineer_runner).__name__)
        )
        engineer_policy = effective_role_session_policy(
            (
                "fresh"
                if supervised_config.engineer_operation == "narrative_edit"
                else supervised_config.role_session_policy
            ),
            engineer_backend,
            # Pi deliberately uses --no-session in isolated maintenance
            # worktrees, so no persisted thread may be passed there.
            allow_resume=not self.engineer_config.isolate_workdir,
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
        reviewer_policy = effective_role_session_policy(
            supervised_config.role_session_policy,
            reviewer_backend,
        )
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
                return enforce_terminal_question_policy(
                    ("error", state.rounds, "", reason, None),
                    supervised_config,
                )
        round_indices = (
            itertools.count(1)
            if supervised_config.max_rounds <= 0
            else range(1, supervised_config.max_rounds + 1)
        )
        for round_index in round_indices:
            engineer_resume_id = state.engineer_session.prepare(
                max_turns=supervised_config.role_session_max_turns,
                max_input_tokens=supervised_config.role_session_max_input_tokens,
            )
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
            if control.action == "return":
                return enforce_terminal_question_policy(
                    control.terminal,
                    supervised_config,
                )
            if control.action == "continue_loop":
                continue

            control = self._handle_agent_driven_wait(
                round_index=round_index,
                supervised_config=supervised_config,
                raw_engineer_message=(
                    outcome.engineer_result.last_agent_message
                    or outcome.raw_engineer_message
                ),
                workdir=workdir,
                state=state,
                on_event=on_event,
            )
            if control.action == "return":
                return enforce_terminal_question_policy(
                    control.terminal,
                    supervised_config,
                )
            if control.action == "continue_loop":
                continue

            control = self._handle_progress_and_self_review(
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                continue_adaptor=continue_adaptor,
                on_event=on_event,
            )
            if control.action == "return":
                return enforce_terminal_question_policy(
                    control.terminal,
                    supervised_config,
                )
            if control.action == "continue_loop":
                continue

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
                prepare_review_context=prepare_review_context,
                on_event=on_event,
            )
            if control.action == "return":
                return enforce_terminal_question_policy(
                    control.terminal,
                    supervised_config,
                )
            if control.action == "continue_loop":
                continue
            review = control.payload

            control = self._settle_round(
                review=review,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                continue_adaptor=continue_adaptor,
                on_event=on_event,
            )
            if control.action == "return":
                return enforce_terminal_question_policy(
                    control.terminal,
                    supervised_config,
                )
            if control.action == "continue_loop":
                continue
            # else "proceed": fall through to the next round.

        # Only an explicit positive max_rounds can exhaust the finite range.
        return enforce_terminal_question_policy(
            (
                "max_rounds",
                state.rounds,
                state.last_engineer_message,
                f"Hit max_rounds={supervised_config.max_rounds} without "
                "reviewer-confirmed completion.",
                None,
            ),
            supervised_config,
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
        hard_idle_seconds: int | None = None
        if supervised_config is not None:
            hard_idle_seconds = int(supervised_config.runner_hard_idle_seconds or 0)
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
                    skill_paths=self.engineer_config.skill_paths,
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    sandbox_mode=self.engineer_config.sandbox_mode,
                    isolate_workdir=self.engineer_config.isolate_workdir,
                    working_dir=str(workdir),
                    live_search=_engineer_live_search(
                        self.engineer_config.vertical_state_root or workdir,
                        self.engineer_config.live_search_stages,
                    ),
                    # Provider/process liveness belongs to the backend's stream
                    # watchdog. Do not infer semantic progress from project
                    # mtimes or provider-private session files.
                    external_interrupt_reason_provider=None,
                    watchdog_hard_idle_seconds=hard_idle_seconds,
                ),
                run_label=run_label,
                resume_thread_id=resume_thread_id,
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
    "parse_continue_work_request",
    "should_clear_thread_id_after_outcome",
]
