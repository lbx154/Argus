"""Mandatory durable accounting registration through execution's admission contract."""
from ...core.dispatch_admission import AccountingAdmission


def accounting_admission(ctx, model: str) -> AccountingAdmission:
    from ...core.cost_control import (
        accounting_integrity_preflight,
        cost_control_enabled,
        reserve_call_budget,
    )
    reservation, reason = reserve_call_budget(
        call_id=ctx.call_id, project_root=ctx.usage_project_root,
        mission_id=ctx.usage_mission_id, provider=ctx.backend._backend_name,
        model=model, run_label=ctx.run_label, global_root=ctx.usage_global_root,
        enforce_policy=cost_control_enabled(),
        execution_project_root=ctx.execution_project_root,
    )

    def before_dispatch():
        if reservation is None:
            raise RuntimeError("denied accounting admission has no dispatch permission")
        accounting_integrity_preflight(project_root=ctx.usage_project_root,
                                       global_root=ctx.usage_global_root,
                                       execution_project_root=ctx.execution_project_root)
        reservation.mark_execution_started()

    def execution_failed(reason):
        if reservation is None:
            raise RuntimeError("denied accounting admission has no finalizer obligation")
        if not reservation._closed:
            reservation.finalization_failed(reason)

    return AccountingAdmission(allowed=reservation is not None, reservation=reservation,
        reason=reason, before_dispatch=before_dispatch, execution_failed=execution_failed,
        report_budget_events=reservation.enforce_policy if reservation is not None else False)
