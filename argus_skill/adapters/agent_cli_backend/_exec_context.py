"""Per-call execution context shared across all agent-CLI execution phases.

A single :class:`_ExecContext` instance is constructed at the top of
:func:`._exec.execute` before any phase is entered.  Each phase receives
the same instance, reads the fields it needs, and writes to the mutable
admission-state fields (``cost_reservation``, ``*_permit``, …) that later
phases depend on.  This removes the need for nested closures to capture a
large set of per-call local variables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.models import RunnerOptions
    from ._core import AgentCliBackend


@dataclass
class _ExecContext:
    """Typed container for all per-call mutable state shared across phases.

    Fields are grouped by lifecycle:

    * *Call identity* — set once at construction, never mutated.
    * *Per-call metadata* — set once at construction.
    * *Usage routing* — set once at construction.
    * *Admission state* — ``None`` at construction; populated by
      :func:`._exec_admission.admit` and read by
      :func:`._exec_finalize.finalize_result`.
    """

    # ------------------------------------------------------------------ #
    # Call identity (immutable after construction)                         #
    # ------------------------------------------------------------------ #
    backend: "AgentCliBackend"
    prompt: str
    options: "RunnerOptions"   # already resolved by backend
    run_label: str
    resume_thread_id: str | None

    # ------------------------------------------------------------------ #
    # Per-call metadata                                                    #
    # ------------------------------------------------------------------ #
    call_id: str
    started_at: float
    log_path: "Path | None"
    io_mode: str

    # ------------------------------------------------------------------ #
    # Usage routing                                                        #
    # ------------------------------------------------------------------ #
    usage_project_root: "Path | None"
    usage_mission_id: str | None
    usage_global_root: "Path | None"

    # ------------------------------------------------------------------ #
    # Mutable: populated during the admission phase                        #
    # ------------------------------------------------------------------ #
    cost_reservation: Any = field(default=None)
    copilot_permit: Any = field(default=None)
    codex_permit: Any = field(default=None)
    codex_quota_active: bool = field(default=False)
    quota_permit: Any = field(default=None)
    event_permit: Any = field(default=None)
