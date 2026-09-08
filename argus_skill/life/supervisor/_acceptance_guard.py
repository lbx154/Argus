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
    validate_assessment,
)
from ...core.models import RunnerOptions
from ...core.operator_messages import uses_cjk
from ...core.run_gateway import run_exec
from ...core.stop_kinds import normalize_stop_kind, pause_status_for_stop_kind


def _stopped(
    reason: str, *, stop_kind: str | None = None, assessment=None, operator_question: str = "",
):
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
        final_review_next_action=operator_question if contract_blocked else "",
        operator_question=operator_question if contract_blocked else "",
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
    chinese = uses_cjk(str(contract.get("original_objective") or contract.get("objective") or ""))
    unavailable = (
        "任务开始前的内部检查暂时无法运行，已有工作已保留。"
        if chinese else "The internal check before starting work is temporarily unavailable; existing work is preserved."
    )

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
            if assessment["result"] == "unresolved" and not assessment["dependencies"]:
                assessment = None
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
                unavailable,
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
                prompt = assessment_prompt(contract)
                for attempt in range(2):
                    if attempt:
                        _current, valid = check_claim()
                        if not valid:
                            return _superseded_outcome()
                    result = run_exec(
                        backend, prompt=prompt, options=options,
                        run_label="planner.acceptance_dependencies",
                    )
                    kind = normalize_stop_kind(getattr(result, "stop_kind", None))
                    if kind or getattr(result, "exit_code", 0) or getattr(result, "fatal_error", None):
                        return stopped_if_current(
                            str(getattr(result, "fatal_error", "") or "Planner dependency assessment was interrupted."),
                            stop_kind=kind or "transient_error",
                        )
                    messages = getattr(result, "agent_messages", None) or []
                    try:
                        candidate = parse_assessment(str(messages[-1] if messages else ""), contract)
                        if candidate["result"] == "unresolved" and not candidate["dependencies"]:
                            raise ValueError("no grounded relationship requiring an operator decision")
                        assessment = candidate
                        break
                    except (ValueError, TypeError):
                        if not attempt:
                            prompt += (
                                "\nYour previous interpretation did not establish a valid explicit relationship. "
                                "Re-read the contract; do not invent receipt files or identifiers for ordinary "
                                "review or progress notes. With no receipt-inclusion requirement, return "
                                "DEPENDENCY_STATUS=assessed and explain non-applicability without relation blocks. "
                                "Use exact quotes and real named paths only for an explicit requirement."
                            )
        except Exception:  # noqa: BLE001 - backend outage is not a contract verdict
            return stopped_if_current(
                unavailable,
                stop_kind="backend_unavailable",
            )
        if assessment is None:
            # An interpreter/format failure is internal. It is not evidence of
            # an ambiguous operator requirement and must never create a popup
            # asking the user to design our receipt schema or storage layout.
            return stopped_if_current(
                "内部检查重试后仍未完成，已有论文和修改均已保留。"
                if chinese else "The internal check did not finish after a retry; existing work is preserved.",
                stop_kind="backend_unavailable",
            )
    current, recorded = check_claim(assessment)
    if not recorded:
        return _superseded_outcome()
    item.manager_decision = current.manager_decision
    if assessment["result"] == "well_founded":
        return None
    if assessment["cycle"]:
        reason = (
            "报告要求包含它自己的本次审阅结论，但写入结论又会改变刚审过的报告。"
            if chinese else "The report must contain the result of reviewing itself, but inserting that result changes the reviewed report."
        )
        question = (
            "可以把这份报告的审阅结论单独保存吗？这样报告定稿后，审阅结果就不会因写回报告而失效。"
            if chinese else "May the review result be saved separately, so adding it does not change the report that was reviewed?"
        )
    else:
        reason = (
            "还需要确定报告中要引用哪一次审阅的结论。"
            if chinese else "It is not yet clear which review result the report should include."
        )
        question = (
            "报告需要包含这次审阅的结论，还是引用此前已完成的审阅记录？"
            if chinese else "Should the report include this review's result or refer to an earlier completed review?"
        )
    return _stopped(reason, assessment=assessment, operator_question=question)
