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
from ...core.secret_guard import known_secret_values
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
    backend._known_secret_values = known_secret_values()
    # Pin Codex's implicit config model before any accounting or execution.
    # The generated command, reservation, and settled usage record therefore
    # share one model id instead of independently guessing after the call.
    options = backend._resolve_execution_options(options)
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

    return spawn_and_finish(ctx, cli_options)
