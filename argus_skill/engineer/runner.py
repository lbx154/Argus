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

import logging
import time  # noqa: F401 - historical test seam for round timing
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
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
)
from ..reviewer import Reviewer, ReviewerConfig
from .checkpoint import ensure_shared_checkpoint

log = logging.getLogger(__name__)
# Config dataclasses are re-exported here for historical/test imports.
from .round_config import (
    EngineerConfig,
    SupervisedConfig,
    _engineer_live_search,
    parse_continue_work_request,
)
from .round_execution import RoundExecutionMixin
from .round_prompt import RoundPromptMixin
from .round_reviewer import RoundReviewerMixin
from .round_self_review import RoundSelfReviewMixin
from .round_settlement import RoundSettlementMixin

# The following ``round_signals`` re-exports are not called internally by
# this module anymore (their logic now lives in the phase mixins above), but
# they are kept importable here — as plain module attributes — for
# historical/test module-attribute access (e.g. ``runner_module._plan_signal_event``,
# direct ``from argus_skill.engineer.runner import _apply_round_secret_guard``).
from .round_signals import (
    _apply_round_secret_guard,  # noqa: F401
    _normalize_dynamic_plan_mode,  # noqa: F401
    _pause_decision_clock,  # noqa: F401
    _review_event_payload,  # noqa: F401
    _run_background_wait,  # noqa: F401
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
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action, include_static)`` is called once
        per round. Round 1 receives the static task/skill contract; continuation
        rounds default to a compact Reviewer delta plus CHECKPOINT.md. Engineer
        and Reviewer both start fresh provider sessions every round; raw model
        threads are never carried across a round or mission boundary.

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
                raw_on_event(
                    redact_secrets_record(
                        event,
                        known_values=known_secret_values(),
                    )
                )

            on_event = _redacted_on_event
        state = RoundLoopState()
        # ``seed_thread_id`` is intentionally ignored: autonomous role calls are
        # one turn per provider session. The checkpoint file is the baton.
        _ = seed_thread_id
        checkpoint_path = ensure_shared_checkpoint(supervised_config.checkpoint_path)
        for round_index in range(1, supervised_config.max_rounds + 1):
            engineer_prompt = self._assemble_round_prompt(
                round_index=round_index,
                supervised_config=supervised_config,
                engineer_prompt_builder=engineer_prompt_builder,
                reviewer_next_action=state.reviewer_next_action,
                checkpoint_path=checkpoint_path,
                workdir=workdir,
                on_event=on_event,
            )

            outcome = self._run_engineer_turn(
                round_index=round_index,
                engineer_prompt=engineer_prompt,
                workdir=workdir,
                supervised_config=supervised_config,
                checkpoint_path=checkpoint_path,
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
                return control.terminal
            if control.action == "continue_loop":
                continue

            control = self._handle_agent_driven_wait(
                round_index=round_index,
                supervised_config=supervised_config,
                raw_engineer_message=outcome.raw_engineer_message,
                workdir=workdir,
                state=state,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue

            control = self._handle_progress_and_self_review(
                round_index=round_index,
                supervised_config=supervised_config,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
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
                return control.terminal
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
                return control.terminal
            if control.action == "continue_loop":
                continue
            # else "proceed": fall through to the next round.

        return (
            "max_rounds",
            state.rounds,
            state.last_engineer_message,
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
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    sandbox_mode=self.engineer_config.sandbox_mode,
                    isolate_workdir=self.engineer_config.isolate_workdir,
                    working_dir=str(workdir),
                    live_search=_engineer_live_search(
                        workdir, self.engineer_config.live_search_stages
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
