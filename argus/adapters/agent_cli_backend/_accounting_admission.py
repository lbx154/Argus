"""Baseline monetary adapter; replaced by the separately reviewed accounting patch.

Preserves the existing configured policy, including its known legacy limitations.
This adapter does not certify historical bills or safe legacy cutover.
"""
from ...core.dispatch_admission import AccountingAdmission


def _checked_legacy_admission() -> None:
    # Baseline adapter has no second-phase integrity protocol. Not cutover-safe.
    pass


def _legacy_execution_failure(reason: str) -> None:
    # Baseline has no durable finalizer protocol. Never delete its reservation.
    pass


def accounting_admission(ctx, model: str) -> AccountingAdmission:
    from ...core.cost_control import cost_control_enabled, reserve_call_budget
    if not cost_control_enabled():
        return AccountingAdmission(allowed=True, report_budget_events=False,
            before_dispatch=_checked_legacy_admission,
            execution_failed=_legacy_execution_failure)
    reservation, reason = reserve_call_budget(
        call_id=ctx.call_id, project_root=ctx.usage_project_root,
        mission_id=ctx.usage_mission_id, provider=ctx.backend._backend_name,
        model=model, run_label=ctx.run_label, global_root=ctx.usage_global_root,
    )
    return AccountingAdmission(allowed=reservation is not None,
                               reservation=reservation, reason=reason,
                               before_dispatch=_checked_legacy_admission,
                               execution_failed=_legacy_execution_failure)
