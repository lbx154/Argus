"""Round-loop phase: agent-driven background/external-work cadence waits.

The Engineer requests a wait through an exact JSON object on the final non-empty
response line. The harness validates the registry id, then monitors that owner
until its state changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core import process_stop
from .external_work import (
    ExternalWorkStatus,
    inspect_external_work,
    parse_external_wait_request,
)
from .round_signals import _pause_decision_clock
from .round_state import (
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
    control_return,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig


class RoundWaitsMixin:
    """Mixin providing ``SupervisedEngineer``'s agent-driven wait phase."""

    @staticmethod
    def _external_work_resume_key(work: ExternalWorkStatus) -> tuple[str, str, str]:
        return (
            work.source, work.work_id,
            work.run_id or str(work.started_at or work.heartbeat_at),
        )

    def _prepare_external_work_followup(
        self, state: RoundLoopState, work: ExternalWorkStatus,
    ) -> None:
        state.backend_failure_streak = 0
        state.backend_failure_signature = ""
        state.backend_failure_same_cause_streak = 0
        if not work.waitable:
            state.external_work_resumptions.add(self._external_work_resume_key(work))
        run = f" (run `{work.run_id}`)" if work.run_id else ""
        state.pending_external_work_followup = (
            "## External work follow-up\n"
            f"You requested a wait for `{work.work_id}`{run}. "
            f"Its last observed runtime state is `{work.state.value}`.\n"
            "Inspect the existing job record and outputs. Use its results or "
            "failure evidence to continue the work you deferred, update the "
            "relevant artifacts and checkpoint, and then return for review."
        )

    def _handle_agent_driven_wait(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        raw_engineer_message: str,
        workdir: Path,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        wait_request = parse_external_wait_request(raw_engineer_message)
        wait_kind, external_work_id = wait_request or ("", "")
        external_work = (
            inspect_external_work(workdir, external_work_id) if external_work_id else None
        )
        source_matches = (
            external_work is not None
            and (
                (wait_kind == "external_work" and external_work.source != "subagent")
                or (
                    wait_kind == "subagent"
                    and supervised_config.background_subagent_advisory
                    and external_work.source == "subagent"
                )
            )
        )
        if source_matches and not external_work.waitable:
            if process_stop.stop_requested():
                session = state.engineer_session
                return control_return((
                    "paused_daemon_shutdown", state.rounds, raw_engineer_message,
                    "daemon shutdown requested during external-work wait",
                    str(getattr(session, "thread_id", "") or "") or None,
                ))
            key = self._external_work_resume_key(external_work)
            if key not in state.external_work_resumptions:
                self._prepare_external_work_followup(state, external_work)
                if on_event:
                    on_event({
                        "type": "round.external_work_wait.completed",
                        "round_index": round_index,
                        "round_max": supervised_config.max_rounds,
                        "work_id": external_work.work_id,
                        "reason": "work_already_changed",
                        "waited_total_s": 0.0,
                        "text": (
                            "The requested background work changed before the wait; "
                            "Engineer will handle its current result before review."
                        ),
                    })
                return control_continue_loop()
        if source_matches and external_work.waitable:
            from . import runner as _runner_module

            wait_reason, waited_s = _runner_module._run_external_work_wait(
                workdir=workdir,
                work_id=external_work.work_id,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                on_event=on_event,
                waited_total_s=0.0,
            )
            state.last_decision_progress_at = _pause_decision_clock(
                state.last_decision_progress_at,
                waited_s,
            )
            if wait_reason == "cadence_elapsed" and not process_stop.stop_requested():
                session = state.engineer_session
                return control_return((
                    "paused_external_work",
                    state.rounds,
                    raw_engineer_message,
                    (
                        f"healthy {wait_kind} {external_work.work_id} is still "
                        "running; released the mission slot"
                    ),
                    str(getattr(session, "thread_id", "") or "") or None,
                ))
            if wait_reason == "stop_requested" or process_stop.stop_requested():
                session = state.engineer_session
                return control_return((
                    "paused_daemon_shutdown",
                    state.rounds,
                    raw_engineer_message,
                    "daemon shutdown requested during external-work wait",
                    str(getattr(session, "thread_id", "") or "") or None,
                ))
            # The wait ended because the external work changed state, so this
            # round completed without touching the self-review phase — the
            # only other place these counters reset. A turn that asked to
            # wait was not a backend failure, so it ends any run of identical
            # failures: the same-cause count restarts from one if that
            # signature ever returns. Without this reset, a rate limit after
            # the wait would read as the continuation of an outage that ended
            # rounds ago and open the hold on an isolated accident.
            self._prepare_external_work_followup(
                state, inspect_external_work(workdir, external_work_id) or external_work,
            )
            return control_continue_loop()
        return control_proceed()
