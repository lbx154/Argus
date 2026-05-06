"""Runner backends for argus-skill missions.

A *runner* is the seam between :class:`argus_skill.mission.engine.MissionLoopEngine`
and "where the engineer actually executes". The engine treats the runner
as a duck-typed object exposing :meth:`RunBackend.run_exec`. This package
formalises the contract and ships two implementations:

* :class:`argus_skill.adapters.codex_backend.CodexRunnerBackend` — runs
  ``codex exec`` on the host (used by the REPL / mission daemon). Lives
  in ``adapters/`` for historical reasons; it satisfies :class:`RunBackend`.
* :class:`argus_skill.runners.container.ContainerCodexRunner` — runs
  ``codex exec`` inside a Harbor (or Harbor-compatible) container. Used
  by the benchmark harness.

The protocol below is the **single** surface the engine consumes; both
backends conform to it. New backends (e.g. a future Claude-CLI-in-docker
runner) only need to implement this method.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunBackend(Protocol):
    """Duck-typed contract every mission runner satisfies.

    The engine calls ``run_exec`` once per round (and occasionally for
    follow-up phases). Implementations must:

      * Return *promptly* on completion of the engineer turn.
      * Set ``exit_code`` to 0 on success, non-zero on failure, and
        populate ``fatal_error`` for runner-level failures (binary
        missing, container crashed, etc.) — engine treats those as
        terminal regardless of ``exit_code``.
      * Populate ``thread_id`` so the engine can pass it back via
        ``resume_thread_id`` on subsequent rounds (when the backend
        supports session resume).

    The exact result type is duck-typed: the engine reads
    ``last_agent_message``, ``exit_code``, ``thread_id``,
    ``turn_completed``, ``turn_failed``, ``fatal_error``.
    Both :class:`argus_skill.core.models.RunnerResult` and
    :class:`codex_autoloop.models.CodexRunResult` qualify.
    """

    def run_exec(
        self,
        *,
        prompt: str,
        options: Any,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> Any:  # noqa: D401 — duck-typed return
        ...


__all__ = ["RunBackend"]
