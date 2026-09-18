"""Provider execution orchestration for the agent CLI backend.

This module owns the public ``execute()`` entry-point for
:class:`._core.AgentCliBackend`.  The execution logic itself is decomposed
into four phase modules backed by a typed per-call context object:

* :mod:`._exec_context` — :class:`._exec_context._ExecContext`, the typed
  per-call state container shared by all phases.
* :mod:`._exec_admission` — cost reservation, CLI option translation, and
  provider quota permit acquisition.  **Fail-closed**: any exception during
  this phase rejects the call before any subprocess is started.
* :mod:`._exec_spawn` — subprocess execution, result translation, quota
  settlement, and I/O event logging.
* :mod:`._exec_finalize` — secret redaction, usage persistence, cost
  reservation settlement, metric recording, and I/O context close.
  Admission exceptions use its non-accounting result finalizer.

Phase order is enforced by the orchestrator below and must not change:
admission → spawn → finalize (uncertain admission uses redaction/metadata
only, never accounting writes).
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from ...core.models import RunnerOptions, RunnerResult
from ...core.runner_receipts import is_provider_turn_cap_receipt
from ...core.secret_guard import redact_secrets_text
from ._budget_monitor import monitor_budget
from ._exec_admission import admit
from ._exec_context import _ExecContext
from ._exec_spawn import spawn_and_finish

if TYPE_CHECKING:
    from ._core import AgentCliBackend


def execute(
    backend: "AgentCliBackend",
    *,
    prompt: str,
    options: RunnerOptions,
    run_label: str,
    resume_thread_id: str | None = None,
) -> RunnerResult:
    backend._refresh_known_secret_values()
    # Pin Codex's implicit config model before any accounting or execution.
    # The generated command, reservation, and settled usage record therefore
    # share one model id instead of independently guessing after the call.
    options = backend._resolve_execution_options(options)
    from ...trial.client import trial_enabled
    hosted_trial = backend._runner.backend == "copilot" and trial_enabled()
    if hosted_trial:
        from dataclasses import replace

        from ...trial.client import trial_model_options
        try:
            model, effort = trial_model_options(options.model, options.reasoning_effort)
        except ValueError as exc:
            return RunnerResult(exit_code=1, stop_kind="permanent_error", fatal_error=str(exc))
        options = replace(options, model=model, reasoning_effort=effort)
        from ...trial.attention import reason
        if blocked := reason():
            return RunnerResult(exit_code=1, fatal_error=blocked, stop_kind="permanent_error")
    from ...core.workbench_plugins import prepare_plugin_run
    from ...core.dispatch_ownership import resolve_dispatch_project
    from ._exec_finalize import finalize_without_accounting
    # Resolve before plugin setup, log migration, slots, or accounting. No log
    # destination is execution authority. Keep call metadata on rejected binds.
    binding = backend._usage_context_snapshot()
    identity = _ExecContext(backend=backend, prompt=prompt, options=options,
        run_label=run_label, resume_thread_id=resume_thread_id,
        call_id=uuid.uuid4().hex, started_at=time.time(), log_path=None, io_mode="",
        usage_project_root=binding[0], usage_mission_id=binding[1],
        usage_global_root=binding[2])
    try:
        project_root = resolve_dispatch_project(project=binding[0], root=binding[2],
            working_dir=None if options.disable_tools and binding[0] is not None else options.working_dir,
            require_project=getattr(backend, "_require_project", False))
        from ...core.dispatch_safety import assert_project_dispatch
        assert_project_dispatch(project_root)
    except Exception as exc:
        return finalize_without_accounting(identity, RunnerResult(exit_code=-1,
            fatal_error=f"dispatch ownership unavailable: {type(exc).__name__}: {exc}",
            stop_kind="backend_unavailable"))
    identity.execution_project_root = project_root
    if project_root is not None:
        identity.usage_project_root = project_root
    prompt, options = prepare_plugin_run(prompt, options,
        backend=backend._runner.backend, run_label=run_label, project_root=project_root)
    backend._plugin_execution_options = options
    try:
        if hosted_trial:
            # A plugin may apply its own explicit model override while binding
            # tools. Validate that final selection before accounting/spawn too.
            try:
                model, effort = trial_model_options(options.model, options.reasoning_effort)
            except ValueError as exc:
                return RunnerResult(exit_code=1, stop_kind="permanent_error", fatal_error=str(exc))
            options = replace(options, model=model, reasoning_effort=effort)
        backend._plugin_execution_options = options
        result = _execute_prepared(backend, prompt=prompt, options=options, run_label=run_label, resume_thread_id=resume_thread_id, identity=identity)
        if (
            hosted_trial
            and (result.exit_code != 0 or result.fatal_error)
            and result.stop_kind not in {
                "budget_exhausted", "cost_unreconciled", "operator_pause", "operator_abort", "daemon_shutdown",
            }
            and not is_provider_turn_cap_receipt(result.fatal_error)
        ):
            from ...trial.attention import record_failure

            # A local turn allowance belongs to the checkpoint/continuation
            # handler, not the account-wide no-replay gate. Actual provider or
            # ambiguous request failures still pause, but retain their cause.
            diagnostic = redact_secrets_text(
                str(result.fatal_error or ""),
                known_values=getattr(backend, "_known_secret_values", ()),
            ).strip()
            result.fatal_error = record_failure(diagnostic)
            if diagnostic:
                result.fatal_error += "\n具体原因：" + diagnostic[:1200]
            result.stop_kind = "permanent_error"
        return result
    finally:
        from ...core.workbench_plugins import finish_plugin_run
        try:
            finish_plugin_run(options)
        finally:
            backend._plugin_execution_options = None


def _execute_prepared(backend, *, prompt, options, run_label, resume_thread_id, identity):
    # Reset per-call: the flag is checked AFTER this call completes,
    # so stale True from a previous call cannot stick across missions.
    backend._auth_failure_detected = False
    call_id = identity.call_id
    started_at = identity.started_at
    log_path = backend._agent_io_log_path(options)
    usage_project_root, usage_mission_id, usage_global_root = (
        identity.usage_project_root, identity.usage_mission_id, identity.usage_global_root
    )
    if usage_project_root is None and log_path is not None:
        usage_project_root = log_path.parent
    from ...core.workbench_plugins import plugin_accounting_root
    accounting_root = plugin_accounting_root(usage_project_root)
    if accounting_root is not None:
        usage_global_root = accounting_root
    ctx = _ExecContext(
        backend=backend,
        prompt=prompt,
        options=options,
        run_label=run_label,
        resume_thread_id=resume_thread_id,
        call_id=call_id,
        started_at=started_at,
        log_path=log_path,
        io_mode="",
        usage_project_root=usage_project_root,
        usage_mission_id=usage_mission_id,
        usage_global_root=usage_global_root,
        execution_project_root=identity.execution_project_root,
    )

    from ...advisor.runtime import advisor_run
    from ...life.experience_runtime import experience_run
    from ...messaging.runtime import peer_run
    from ...skills.runtime_tools_context import runtime_tools_run

    with advisor_run(ctx), peer_run(ctx), experience_run(ctx), runtime_tools_run(ctx):
        io_context = backend._io_logger.start_call(
            call_id=call_id, run_label=run_label, log_path=log_path,
            model=ctx.options.model, prompt=ctx.prompt,
        )
        ctx.io_mode = io_context["mode"]
        from ...core.paths import global_root
        from ...core.provider_slots import acquire_provider_slot, release_provider_slot
        from ._exec_finalize import finalize_result

        try:
            slot, reason = acquire_provider_slot(ctx.usage_global_root or global_root())
        except Exception as exc:  # noqa: BLE001 - fail closed before provider spawn
            reason = f"provider admission unavailable: {type(exc).__name__}: {exc}"
            return finalize_result(ctx, RunnerResult(exit_code=-1, fatal_error=reason,
                stop_kind="backend_unavailable"), status="denied", error=reason)
        if reason:
            return finalize_result(ctx, RunnerResult(exit_code=-1, fatal_error=reason,
                stop_kind="provider_cooldown"), status="denied", error=reason)
        try:
            cli_options, denied = admit(ctx)
            if denied is not None:
                return denied

            from contextlib import ExitStack

            from ...core.dispatch_safety import provider_dispatch_guard
            from ...trial.training_runtime import capture_runtime_call
            with ExitStack() as stack:
                try:
                    # Revalidate ownership immediately before the guarded lease;
                    # a changed workdir/session alias must not switch authorities.
                    from ...core.dispatch_ownership import resolve_dispatch_project
                    owner = resolve_dispatch_project(project=ctx.execution_project_root,
                        root=identity.usage_global_root,
                        working_dir=None if ctx.options.disable_tools and identity.usage_project_root is not None else ctx.options.working_dir,
                        require_project=getattr(backend, "_require_project", False))
                    if owner != ctx.execution_project_root:
                        raise RuntimeError("execution ownership changed before dispatch")
                    stack.enter_context(provider_dispatch_guard(owner))
                    from ...core.dispatch_admission import AccountingAdmission
                    AccountingAdmission.validate(ctx.accounting_admission)
                    if ctx.accounting_admission.allowed is not True:
                        raise RuntimeError("accounting permission changed before dispatch")
                    ctx.accounting_admission.before_dispatch()
                except Exception as exc:  # fail closed if paused after reservation
                    reason = f"dispatch safety unavailable: {type(exc).__name__}: {exc}"
                    return finalize_result(ctx, RunnerResult(exit_code=-1, fatal_error=reason,
                        stop_kind="backend_unavailable"), status="denied", error=reason)
                try:
                    with monitor_budget(ctx, cli_options), capture_runtime_call(ctx, cli_options):
                        return spawn_and_finish(ctx, cli_options)
                except BaseException as exc:
                    if ctx.accounting_admission is not None:
                        try:
                            ctx.accounting_admission.execution_failed(
                                f"provider execution: {type(exc).__name__}: {exc}")
                        except Exception:
                            pass  # original obligation + released lease remain debt
                    raise  # preserve typed auth/cancellation behavior
        finally:
            release_provider_slot(slot)
