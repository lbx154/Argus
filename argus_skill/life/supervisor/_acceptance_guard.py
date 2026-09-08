"""One durable dependency assessment before executing a suspicious closeout task."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any

from ...core.acceptance_dependencies import (
    CACHE_KEY,
    SCHEMA_VERSION,
    assessment_prompt,
    contract_fingerprint,
    mission_acceptance_contract,
    needs_dependency_assessment,
    parse_assessment,
    unresolved_assessment,
    validate_assessment,
)
from ...core.models import RunnerOptions
from ...core.run_gateway import run_exec
from ...core.stop_kinds import normalize_stop_kind, pause_status_for_stop_kind


def _stopped(reason: str, *, stop_kind: str | None = None, assessment=None):
    contract_blocked = assessment is not None
    return SimpleNamespace(
        success=False,
        status=("blocked" if contract_blocked else (
            pause_status_for_stop_kind(stop_kind)
            or {"operator_abort": "aborted", "backend_unavailable": "infra_blocked"}.get(
                stop_kind, "error",
            )
        )),
        stop_reason=reason, stop_kind=stop_kind, recoverable=True, rounds=0,
        final_review_status="not_assessed", final_review_source="contract_preflight",
        final_review_reason=reason,
        final_review_next_action=(
            "Clarify or revise the acceptance dependency before execution."
            if contract_blocked else ""
        ),
        operator_question=(
            "The acceptance condition has no confirmed stable closeout. "
            "Should the current review receipt be checked outside the reviewed artifact, "
            "or should an embedded receipt refer to a fixed prior review? "
            "Please clarify the acceptance boundary; Argus has kept the original requirement."
            if contract_blocked else ""
        ),
        operator_options=[], final_message=reason, summary=reason,
        final_planner_report=(
            {"authority_impact": "operator", "forward_progress": False,
             "acceptance_dependency_assessment": assessment}
            if contract_blocked else {}
        ),
    )


def _superseded_outcome():
    outcome = _stopped(
        "The mission contract or running state changed during acceptance assessment; "
        "the superseded result was discarded and the current task remains with its owner.",
    )
    outcome.status = "claim_lost"
    outcome.acceptance_assessment_superseded = True
    return outcome


def acceptance_guard_outcome(supervisor: Any, state: Any) -> Any | None:
    """Return a recoverable hold only for a proven cycle or unresolved candidate.

    Existing task creation retains the natural-language contract in the backlog.
    This entry point covers Manager/Planner-created and legacy tasks alike, before
    either an Engineer round or a new independent review can be dispatched.
    """
    item = state.item
    contract = mission_acceptance_contract(item)
    if not needs_dependency_assessment(contract):
        return None
    fingerprint = contract_fingerprint(contract)

    def check_claim(assessment=None):
        return supervisor.memory.backlog.record_acceptance_dependency_assessment(
            item.id, expected_fingerprint=fingerprint, assessment=assessment,
            expected_started_ts=item.started_ts, expected_owner=item.running_owner,
        )

    def stopped_if_current(reason: str, *, stop_kind: str):
        # Errors and cancellations can race a new task version or operator
        # abort just like a usable reply. Validate ownership before settling
        # them too, while leaving provider errors out of the assessment cache.
        _current, valid = check_claim()
        return _stopped(reason, stop_kind=stop_kind) if valid else _superseded_outcome()

    decision = dict(item.manager_decision or {})
    cached = decision.get(CACHE_KEY)
    assessment = None
    if isinstance(cached, dict) and (
        cached.get("schema_version") == SCHEMA_VERSION
        and cached.get("contract_fingerprint") == fingerprint
    ):
        try:
            # Rebuild the graph; do not trust a stored/model-supplied result flag.
            assessment = validate_assessment(cached, contract)
        except ValueError:
            pass
    if assessment is None:
        runner = supervisor.runner
        backend = (
            getattr(runner, "planner_backend", None)
            or getattr(runner, "backend", None)
            or getattr(runner, "_backend", None)
            or getattr(getattr(supervisor, "planner", None), "runner", None)
        )
        if backend is None or not callable(getattr(backend, "run_exec", None)):
            return stopped_if_current(
                "Planner backend unavailable for acceptance dependency assessment.",
                stop_kind="backend_unavailable",
            )
        config = supervisor._planner_config()
        options = RunnerOptions(
            model=config.model, reasoning_effort=config.reasoning_effort,
            working_dir=str(state.execution_workdir or supervisor._project_workdir()),
            disable_tools=True, sandbox_mode="read-only", force_safe_mode=True,
            dangerous_yolo=False, full_auto=False, skip_git_repo_check=True,
            external_interrupt_reason_provider=config.external_interrupt_reason_provider,
        )
        try:
            with ExitStack() as stack:
                usage_scope = getattr(runner, "task_usage_context", None)
                if callable(usage_scope):
                    stack.enter_context(usage_scope(state.usage_attempt_id))
                stream_scope = getattr(runner, "stream_to", None)
                if callable(stream_scope):
                    stack.enter_context(stream_scope(state.cost_sink))
                if hasattr(runner, "_active_mission_id"):
                    previous = runner._active_mission_id
                    runner._active_mission_id = item.id
                    stack.callback(setattr, runner, "_active_mission_id", previous)
                result = run_exec(
                    backend, prompt=assessment_prompt(contract), options=options,
                    run_label="planner.acceptance_dependencies",
                )
        except Exception:  # noqa: BLE001 - backend outage is not a contract verdict
            return stopped_if_current(
                "Planner acceptance dependency assessment is unavailable; restore the provider to resume.",
                stop_kind="backend_unavailable",
            )
        kind = normalize_stop_kind(getattr(result, "stop_kind", None))
        if kind or getattr(result, "exit_code", 0) or getattr(result, "fatal_error", None):
            return stopped_if_current(
                str(getattr(result, "fatal_error", "") or "Planner dependency assessment was interrupted."),
                stop_kind=kind or "transient_error",
            )
        messages = getattr(result, "agent_messages", None) or []
        try:
            assessment = parse_assessment(str(messages[-1] if messages else ""), contract)
        except (ValueError, TypeError):
            assessment = unresolved_assessment(
                contract,
                "Planner could not establish receipt placement, freshness, and subject "
                "with exact evidence from the acceptance contract.",
            )
    current, recorded = check_claim(assessment)
    if not recorded:
        return _superseded_outcome()
    item.manager_decision = current.manager_decision
    if assessment["result"] == "well_founded":
        return None
    reason = (
        "Acceptance dependency cycle: " + " → ".join(assessment["cycle"])
        + ". A current review receipt is produced after reviewing the artifact; "
        "embedding that receipt changes the artifact and invalidates the same review."
        if assessment["cycle"]
        else "Acceptance dependency needs clarification: " + assessment["reason"]
    )
    return _stopped(reason, assessment=assessment)
