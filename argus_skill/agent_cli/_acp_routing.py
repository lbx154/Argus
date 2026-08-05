"""Warm-``copilot --acp``-client fast path for Manager control-plane labels.

Routes a small allowlist of run labels (Manager classify + operator chat)
through a persistent ACP process instead of spawning a fresh one-shot CLI per
turn. Every other role (engineer/reviewer/planner/mission) stays on the
ordinary ``Popen`` path in ``_run_exec.py``. Extracted verbatim from
``agent_cli_runner.py``.
"""

from __future__ import annotations

import os

from .models import AgentRunResult
from .runner_backend import BACKEND_COPILOT

# Manager run labels routed through the warm ``copilot --acp`` client (see
# ``_acp_enabled``). Front-door classification, handoff classification, and
# operator-facing conversation use separate ACP sessions; each configured
# model/tool policy gets one long-lived process. Mission roles remain on the
# ordinary one-shot CLI path. The set is overridable via
# ``ARGUS_SKILL_COPILOT_ACP_LABELS``.
_ACP_MANAGER_LABELS = frozenset(
    {
        "manager-frontdoor-classify",
        "manager-classify-fast",
        "manager-classify-grounded",
        "manager-quick-reply",
        "simple-1",
        "chat-1",
    }
)

_ACP_LEAN_LABELS = frozenset({
    "manager-frontdoor-classify",
    "manager-classify-fast",
    "manager-quick-reply",
})


class AcpRoutingMixin:
    """Owns the ACP client lifecycle and the fast-path routing decision."""

    def set_acp_scope(self, scope: str) -> None:
        target = str(scope or "").strip() or f"runner:{id(self):x}"
        if target == self._acp_scope:
            return
        from .copilot_acp import close_clients_for_scope

        close_clients_for_scope(self._acp_scope)
        self._acp_scope = target

    def prewarm_acp_client(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        lean: bool,
        cwd: str,
        front_door_session: bool = False,
        read_only: bool = False,
        add_dirs: list[str] | None = None,
    ) -> None:
        from .copilot_acp import get_client

        get_client(
            self.agent_bin,
            model,
            reasoning_effort,
            lean=lean,
            read_only=read_only,
            add_dirs=add_dirs,
            scope=self._acp_scope,
        ).prewarm(cwd, front_door_session=front_door_session)

    def close_acp_clients(self) -> None:
        from .copilot_acp import close_clients_for_scope

        close_clients_for_scope(self._acp_scope)

    def _acp_enabled(
        self,
        run_label: str | None,
        options=None,
    ) -> bool:
        """Route this call through the persistent ``copilot --acp`` client?

        True only for the copilot backend and a Manager label. It defaults ON;
        ``ARGUS_SKILL_COPILOT_ACP=0`` is the explicit rollback switch, while
        ``ARGUS_SKILL_COPILOT_ACP_LABELS`` overrides the default label set. All
        engineer/reviewer/planner/mission turns stay on the CLI ``Popen`` path.
        """
        if self.backend != BACKEND_COPILOT or not run_label:
            return False
        raw_flag = os.environ.get("ARGUS_SKILL_COPILOT_ACP")
        flag = str(raw_flag or "").strip().lower()
        if raw_flag is not None and flag not in ("1", "true", "yes", "on"):
            return False
        raw = os.environ.get("ARGUS_SKILL_COPILOT_ACP_LABELS", "")
        allowed = frozenset(x.strip() for x in raw.split(",") if x.strip()) or _ACP_MANAGER_LABELS
        return run_label in allowed

    def _run_exec_acp(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options,
        run_label: str | None,
    ):
        """Run one prompt on the warm ACP client.

        A failure before an ACP session exists returns ``None`` so the caller can
        safely fall back to the ordinary CLI, except for lean classifiers whose
        no-context policy would be lost. Once a conversational prompt may have
        started, return its failure instead of replaying a tool-capable turn in a
        second process (which could duplicate side effects).
        """
        try:
            from .copilot_acp import get_client

            client = get_client(
                self.agent_bin,
                options.model,
                options.reasoning_effort,
                lean=run_label in _ACP_LEAN_LABELS,
                read_only=getattr(options, "sandbox_mode", None) == "read-only",
                add_dirs=list(getattr(options, "add_dirs", None) or []),
                scope=self._acp_scope,
            )

            def _emit(text: str) -> None:
                self._emit(self._stream_name("stdout", run_label), text)

            result = client.run_prompt(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=options,
                run_label=run_label,
                cwd=options.working_dir,
                emit=_emit,
                on_block=options.on_agent_message,
            )
        except Exception as exc:  # noqa: BLE001 — fast path must never break the turn
            if run_label in _ACP_LEAN_LABELS:
                return AgentRunResult(
                    command=[self.agent_bin, "--acp"],
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    turn_completed=False,
                    turn_failed=True,
                    fatal_error=f"ACP lean classifier unavailable: {exc}",
                )
            return None
        if result.exit_code == 0 and result.turn_completed and result.agent_messages:
            return result
        if run_label in _ACP_LEAN_LABELS:
            return result
        if run_label in {"simple-1", "chat-1"} and result.thread_id:
            return result
        return None
