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
  Called on every exit path including admission denials.

Phase order is enforced by the orchestrator below and must not change:
admission → spawn → finalize (finalize is called from within both admission
and spawn, never skipped).
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
    usage_context = getattr(backend, "_usage_context_snapshot", None)
    project_root = usage_context()[0] if callable(usage_context) else None
    if project_root is None:
        log_factory = getattr(backend, "_agent_io_log_path", None)
        log_path = log_factory(options) if callable(log_factory) else None
        project_root = log_path.parent if log_path is not None else None
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
        result = _execute_prepared(backend, prompt=prompt, options=options, run_label=run_label, resume_thread_id=resume_thread_id)
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


def _execute_prepared(backend, *, prompt, options, run_label, resume_thread_id):
    # Reset per-call: the flag is checked AFTER this call completes,
    # so stale True from a previous call cannot stick across missions.
    backend._auth_failure_detected = False
    call_id = uuid.uuid4().hex
    started_at = time.time()
    log_path = backend._agent_io_log_path(options)
    usage_project_root, usage_mission_id, usage_global_root = (
        backend._usage_context_snapshot()
    )
    if usage_project_root is None and log_path is not None:
        usage_project_root = log_path.parent
    from ...core.workbench_plugins import plugin_accounting_root
    accounting_root = plugin_accounting_root(usage_project_root)
    if accounting_root is not None:
        usage_global_root = accounting_root
    io_context = backend._io_logger.start_call(
        call_id=call_id,
        run_label=run_label,
        log_path=log_path,
        model=options.model,
        prompt=prompt,
    )
    io_mode = io_context["mode"]

    ctx = _ExecContext(
        backend=backend,
        prompt=prompt,
        options=options,
        run_label=run_label,
        resume_thread_id=resume_thread_id,
        call_id=call_id,
        started_at=started_at,
        log_path=log_path,
        io_mode=io_mode,
        usage_project_root=usage_project_root,
        usage_mission_id=usage_mission_id,
        usage_global_root=usage_global_root,
    )

    cli_options, denied = admit(ctx)
    if denied is not None:
        return denied

    from ...trial.training_runtime import capture_runtime_call

    with monitor_budget(ctx, cli_options), capture_runtime_call(ctx, cli_options):
        return spawn_and_finish(ctx, cli_options)
