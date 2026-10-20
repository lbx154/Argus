"""Round-loop phase: agent-driven background/external-work cadence waits.

The Engineer asks for a wait with ``→ wait: <task-id>`` (or the older exact
JSON line). The registry, not the request's spelling, says what that work is;
the harness then monitors it until its state changes.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core import process_stop
from ..core.event_catalog import EventType
from .external_work import (
    ExternalWorkState,
    ExternalWorkStatus,
    inspect_external_work,
    parse_external_wait_request,
    scan_external_work,
)
from .round_settlement import _manager_consult
from .round_signals import _pause_decision_clock
from .round_state import (
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
    control_return,
)

if TYPE_CHECKING:
    from ..core.models import ReviewDecision
    from .runner import SupervisedConfig


# A round waiting on background work -- an Engineer-requested job, or the
# lead's own team -- keeps its mission slot at most this long while the daemon
# has no other use for it; after that the round pauses and the daemon resumes
# the mission when the work settles.
_EXTERNAL_WAIT_HOLD_MAX_ENV = "ARGUS_EXTERNAL_WAIT_HOLD_MAX_SECONDS"
_EXTERNAL_WAIT_HOLD_MAX_DEFAULT = 3600.0


def _external_wait_hold_max_seconds() -> float:
    try:
        return max(0.0, float(
            os.environ.get(_EXTERNAL_WAIT_HOLD_MAX_ENV, "") or _EXTERNAL_WAIT_HOLD_MAX_DEFAULT
        ))
    except ValueError:
        return _EXTERNAL_WAIT_HOLD_MAX_DEFAULT


def _team_wait_line(work_id: str) -> str:
    return json.dumps({"wait_for": "external_work", "wait_id": work_id})


def _keep_holding_external_wait(
    hold: Callable[[], bool] | None, *, elapsed_s: float, budget_s: float,
    workdir: Path, work_id: str,
) -> bool:
    """May the round wait another cadence instead of releasing its slot?

    Only while the daemon says nobody needs the slot, the hold budget has not
    run out, and the work is still healthy. Any doubt releases the slot, which
    is the behaviour every caller had before the hold existed.
    """
    if hold is None or elapsed_s >= budget_s:
        return False
    current = inspect_external_work(workdir, work_id)
    if current is None or not current.waitable:
        return False
    try:
        return bool(hold())
    except Exception:  # noqa: BLE001 - a failing answer is not a reason to hold
        return False


_LEAD_AUTO_WAIT_ENV = "ARGUS_TEAM_LEAD_AUTO_WAIT"
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"


def lead_auto_wait_enabled() -> bool:
    """The lead waits for runtime-owned teams unless disabled or inside a worker.

    A teammate runs the same round loop in the same workspace; it must never
    wait for the team it belongs to.
    """
    if os.environ.get(_TEAM_TASK_ENV, "").strip():
        return False
    raw = os.environ.get(_LEAD_AUTO_WAIT_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def runtime_team_wait_target(workdir: Path) -> ExternalWorkStatus | None:
    """The runtime-owned team the lead should wait for right now, if any."""
    for status in scan_external_work(workdir):
        if status.source == "team" and status.owner == "runtime" and status.waitable:
            return status
    return None


def _ask_manager_about_stalled_work(
    config: "SupervisedConfig", workdir: Path, work: ExternalWorkStatus,
    round_index: int, on_event: Callable[[dict], None] | None,
) -> None:
    """A stalled job is the Manager's to judge, asked once; nothing stops it.

    The Engineer gets the job back with its follow-up either way. The
    Manager's ledger of held facts keeps the same stall from being asked
    about twice: it is asked again only once the work or workspace changes.
    """
    if on_event is None or not getattr(config, "manager_supervised", False):
        return
    consult = _manager_consult(
        config, workdir,
        f"background job `{work.work_id}` has stalled: {work.reason}. Nothing stops it "
        "on its own; CONTINUE leaves it to the Engineer, STEER tells the Engineer what to do with it",
        round_index,
    )
    if consult:
        on_event({
            "type": EventType.ROUND_STALL,
            "round_index": round_index,
            "round_max": config.max_rounds,
            "stall_reason": "background_work_stalled",
            "work_id": work.work_id,
            "consult": consult,
            "text": consult,
        })


class RoundWaitsMixin:
    """Mixin providing ``SupervisedEngineer``'s agent-driven wait phase."""

    def _handle_runtime_team_wait(
        self,
        *,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        state: RoundLoopState,
    ) -> RoundControl:
        """Wait for a runtime-owned team before spending an Engineer round.

        The runtime formed the idea portfolio itself and the Curator staffs it;
        while its workers run, the lead has nothing to do but watch. It used to
        do that with model turns — ``team status``, ``ps``, ``tail`` — and on a
        two-slot host its open call also starved the Planner. Now the harness
        watches the task board instead: no model call until the team finishes
        or needs attention. A finished team ends this attempt with the same
        structured pause an Engineer-requested wait uses, so the daemon resumes
        the mission and the runtime forms the next step (the selector) before
        the Engineer is called.
        """
        if not lead_auto_wait_enabled():
            return control_proceed()
        try:
            target = runtime_team_wait_target(workdir)
        except Exception:  # noqa: BLE001 — an unreadable board never blocks the round
            return control_proceed()
        if target is None:
            return control_proceed()

        from . import runner as _runner_module

        session = state.engineer_session
        thread_id = str(getattr(session, "thread_id", "") or "") or None
        budget = _external_wait_hold_max_seconds()
        started = time.monotonic()
        wait_reason, waited_s = _runner_module._run_external_work_wait(
            workdir=workdir,
            work_id=target.work_id,
            keep_waiting=lambda: time.monotonic() - started < budget,
        )
        state.last_decision_progress_at = _pause_decision_clock(
            state.last_decision_progress_at, waited_s,
        )
        if wait_reason == "stop_requested" or process_stop.stop_requested():
            return control_return((
                "paused_daemon_shutdown", state.rounds, state.last_engineer_message,
                f"daemon shutdown requested while waiting for {target.work_id}",
                thread_id,
            ))
        current = inspect_external_work(workdir, target.work_id)
        if current is not None and current.waitable:
            # Hand the wait to the daemon: it resumes the mission once the team
            # settles, and the mission slot is free meanwhile.
            return control_return((
                "paused_external_work", state.rounds, _team_wait_line(target.work_id),
                f"runtime-owned {target.work_id} is still working; the lead released "
                "the mission slot without spending an Engineer round",
                thread_id,
            ))
        if current is not None and current.state is ExternalWorkState.NEEDS_ATTENTION:
            self._prepare_external_work_followup(state, current)
            state.pending_external_work_followup = (
                "## Team follow-up\n"
                f"The runtime-owned team `{current.work_id}` needs attention: "
                f"{current.reason or current.description}.\n"
                + "\n".join(f"- {fact}" for fact in current.facts)
                + "\nSettle the failed or blocked tasks (retry, answer, or retire them) "
                "before anything else; do not resize the pool or poll the team."
            )
            return control_proceed()
        # Finished (or gone): let the daemon resume this mission so the runtime
        # re-forms the portfolio's next step before the Engineer is called.
        return control_return((
            "paused_external_work", state.rounds, _team_wait_line(target.work_id),
            f"runtime-owned {target.work_id} finished; the mission resumes to settle it",
            thread_id,
        ))

    @staticmethod
    def _wait_review_receipt_path(config: "SupervisedConfig") -> Path | None:
        packet = getattr(config, "context_packet_path", None)
        return Path(packet).parent / "external-wait-reviews.json" if packet else None

    def _external_wait_needs_review(
        self,
        *,
        supervised_config: "SupervisedConfig",
        raw_engineer_message: str,
        workdir: Path,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> bool:
        """Review durable mission run/result handoffs once, never their heartbeat."""
        state.pending_external_wait_review = None
        if process_stop.stop_requested() or not getattr(supervised_config, "require_independent_review", False):
            return False
        # This is a durable four-role mission handoff. Low-level loops without
        # a mission packet retain their existing wait-then-review behavior.
        receipt = self._wait_review_receipt_path(supervised_config)
        if receipt is None:
            return False
        request = parse_external_wait_request(raw_engineer_message)
        status = inspect_external_work(workdir, request[1]) if request else None
        if status is None:
            return False
        work_id = status.work_id
        if status.source == "subagent" and not supervised_config.background_subagent_advisory:
            return False
        if status.waitable and not supervised_config.review_background_launches:
            return False
        if not status.waitable and self._external_work_resume_key(status) not in state.external_work_resumptions:
            # Preserve the upstream result-consumption turn before independent review.
            return False
        identity = {
            "work_id": work_id,
            "run_id": status.run_id,
            "started_at": status.started_at if not status.run_id else None,
            "phase": "waiting" if status.waitable else status.state.value,
            "outcome": "" if status.waitable else status.outcome,
        }
        key = json.dumps(identity, sort_keys=True)
        if receipt.exists():
            try:
                saved = json.loads(receipt.read_text())
                reviewed = saved.get("reviewed", []) if isinstance(saved, dict) else []
                if isinstance(reviewed, list):
                    state.reviewed_external_waits.update(key for key in reviewed if isinstance(key, str))
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # Missing/corrupt receipts never certify a review.
        if key in state.reviewed_external_waits:
            return False
        state.pending_external_wait_review = key
        if on_event:
            on_event({"type": "round.external_work_review.required", **identity, "review_key": key})
        return True

    def _acknowledge_external_wait_review(
        self,
        *,
        supervised_config: "SupervisedConfig",
        state: RoundLoopState,
        review: "ReviewDecision",
        on_event: Callable[[dict], None] | None,
    ) -> None:
        key = state.pending_external_wait_review
        if not key or (review.review_source or "reviewer") != "reviewer":
            return
        state.reviewed_external_waits.add(key)
        receipt = self._wait_review_receipt_path(supervised_config)
        if receipt:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".wait-review-", dir=receipt.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump({"version": 1, "reviewed": sorted(state.reviewed_external_waits)}, stream)
                os.replace(name, receipt)
            finally:
                Path(name).unlink(missing_ok=True)
        if on_event:
            on_event({"type": "round.external_work_review.completed", "review_key": key, "review_status": review.status})

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
        # What the work is comes from the registry, not from how the request
        # was spelled; a subagent is waited on only with its advisory enabled.
        wait_request = parse_external_wait_request(raw_engineer_message)
        external_work = (
            inspect_external_work(workdir, wait_request[1]) if wait_request else None
        )
        if external_work is None or (
            external_work.source == "subagent"
            and not supervised_config.background_subagent_advisory
        ):
            return control_proceed()
        external_work_id = external_work.work_id
        thread_id = str(getattr(state.engineer_session, "thread_id", "") or "") or None
        shutdown = control_return((
            "paused_daemon_shutdown", state.rounds, raw_engineer_message,
            "daemon shutdown requested during external-work wait", thread_id,
        ))
        if not external_work.waitable:
            if process_stop.stop_requested():
                return shutdown
            if self._external_work_resume_key(external_work) in state.external_work_resumptions:
                return control_proceed()
            current = external_work
        else:
            from . import runner as _runner_module

            hold = getattr(supervised_config, "external_wait_hold", None)
            hold_budget = _external_wait_hold_max_seconds()
            hold_started = time.monotonic()
            wait_reason, waited_s = _runner_module._run_external_work_wait(
                workdir=workdir,
                work_id=external_work_id,
                keep_waiting=lambda: _keep_holding_external_wait(
                    hold,
                    elapsed_s=time.monotonic() - hold_started,
                    budget_s=hold_budget,
                    workdir=workdir,
                    work_id=external_work_id,
                ),
            )
            state.last_decision_progress_at = _pause_decision_clock(
                state.last_decision_progress_at, waited_s,
            )
            if wait_reason == "stop_requested" or process_stop.stop_requested():
                return shutdown
            if wait_reason == "cadence_elapsed":
                return control_return((
                    "paused_external_work", state.rounds, raw_engineer_message,
                    f"healthy {external_work.source} {external_work_id} is still "
                    "running; released the mission slot",
                    thread_id,
                ))
            # The wait ended because the external work changed state, so this
            # round completed without touching the self-review phase — the
            # only other place these counters reset. A turn that asked to
            # wait was not a backend failure, so it ends any run of identical
            # failures: the same-cause count restarts from one if that
            # signature ever returns. Without this reset, a rate limit after
            # the wait would read as the continuation of an outage that ended
            # rounds ago and open the hold on an isolated accident.
            current = inspect_external_work(workdir, external_work_id) or external_work
        self._prepare_external_work_followup(state, current)
        if current.state is ExternalWorkState.STALLED:
            _ask_manager_about_stalled_work(
                supervised_config, workdir, current, round_index, on_event,
            )
        return control_continue_loop()
