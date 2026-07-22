"""Foreground Manager routing and bounded SELF reply execution."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core.knobs import resolve_manager_reply_model, resolve_role_reasoning_effort
from ..core.models import RunnerOptions
from ..core.ports import EventSink
from ..core.run_gateway import run_exec as gateway_run_exec
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._env import env_flag, env_int
from ._runtime_backends import _Outcome

_SELF_RETRYABLE_ACP_ERRORS = (
    "acp restart requested",
    "acp process died",
    "stopreason=cancelled",
)


def self_retryable_transport_failure(result: Any) -> bool:
    """Retry only an empty ACP transport failure with no possible side effects."""
    if (getattr(result, "last_agent_message", "") or "").strip():
        return False
    if bool(getattr(result, "tool_activity_observed", False)):
        return False
    fatal = str(getattr(result, "fatal_error", "") or "").strip().casefold()
    if not fatal:
        return int(getattr(result, "exit_code", 0) or 0) == 0
    if fatal.startswith(("external interrupt:", "refused before start:")):
        return False
    return any(marker in fatal for marker in _SELF_RETRYABLE_ACP_ERRORS)


class SelfReplyMixin:
    """Operator-facing Manager front door mixed into ``_SkillLoopRunner``."""

    def _maybe_chat_outcome(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> _Outcome | None:
        workdir = (
            Path(self._args.workdir).expanduser()
            if getattr(self._args, "workdir", None)
            else Path.cwd()
        )
        safe_mode = env_flag("ARGUS_SKILL_SAFE_MODE", False)

        def _classify_run_exec(prompt: str) -> Any:
            return gateway_run_exec(
                self._backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=self._args.engineer_model,
                    reasoning_effort="low",
                    full_auto=safe_mode,
                    skip_git_repo_check=True,
                    dangerous_yolo=not safe_mode,
                    working_dir=str(workdir),
                ),
                run_label="router-classify",
                resume_thread_id=None,
            )

        def _phase(label: str, *, role: str = "manager") -> None:
            if not callable(phase_cb):
                return
            try:
                phase_cb(label, role=role)
                return
            except TypeError:
                pass
            except Exception:  # noqa: BLE001 - UI callbacks never own the turn
                return
            try:
                phase_cb(label)
            except Exception:  # noqa: BLE001
                pass

        class _PhaseSink:
            def __init__(self, inner: EventSink) -> None:
                self._inner = inner

            def handle_event(self, event: dict[str, Any]) -> None:
                event_type = str(event.get("type") or "")
                kind = str(event.get("kind") or "")
                is_reply = event_type == "engineer.progress" and kind in {
                    "assistant_message",
                    "agent_message",
                    "message",
                }
                if event_type == "loop.start":
                    _phase(f"{backend_label} working on your message…")
                elif event_type == "engineer.progress" and not is_reply:
                    summary = str(event.get("action_summary") or "").strip()
                    safe = summary or {
                        "reasoning": "reasoning about the response",
                        "command_execution": "checking project state",
                        "file_change": "preparing a change",
                        "tool_use": "using a tool",
                    }.get(kind, "working on your message")
                    _phase(safe[:80])
                self._inner.handle_event(event)

            def handle_stream_line(self, stream: str, line: str) -> None:
                handler = getattr(self._inner, "handle_stream_line", None)
                if callable(handler):
                    handler(stream, line)

            def close(self) -> None:
                closer = getattr(self._inner, "close", None)
                if callable(closer):
                    closer()

        from ..cli.roles_status import runner_backend_label

        backend_label = runner_backend_label()
        _phase(f"Deciding: {backend_label} solo vs. the Argus team…")
        if route not in ("simple", "complex"):
            if root_task_id is None:
                route = self.manager.route(objective, run_exec=_classify_run_exec)
            else:
                route = self.manager.route(
                    objective,
                    run_exec=_classify_run_exec,
                    root_task_id=root_task_id,
                )
        if route == "simple":
            _phase(f"{backend_label} handling it solo…")
            return self._simple_quick_reply(
                objective=objective,
                sink=_PhaseSink(sink),
                seed_thread_id=seed_thread_id,
            )
        _phase("Handing off to Planner / Engineer / Reviewer…")
        return None

    def classify_needs_continuous(
        self,
        objective: str,
        *,
        root_task_id: str | None = None,
    ) -> bool:
        safe_mode = env_flag("ARGUS_SKILL_SAFE_MODE", False)
        workdir = (
            Path(self._args.workdir).expanduser()
            if getattr(self._args, "workdir", None)
            else Path.cwd()
        )

        def _classify_run_exec(prompt: str) -> Any:
            return gateway_run_exec(
                self._backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=self._args.engineer_model,
                    reasoning_effort="low",
                    full_auto=safe_mode,
                    skip_git_repo_check=True,
                    dangerous_yolo=not safe_mode,
                    working_dir=str(workdir),
                ),
                run_label="router-classify-persistence",
                resume_thread_id=None,
            )

        try:
            with self.task_usage_context(root_task_id):
                return bool(
                    self.manager.needs_persistence(
                        objective,
                        run_exec=_classify_run_exec,
                        root_task_id=root_task_id,
                    )
                )
        except Exception:  # noqa: BLE001 - substantive TEAM work defaults standing
            return True

    def chat_reply_if_conversational(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> bool:
        with self.task_usage_context(root_task_id):
            return self._maybe_chat_outcome(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
                phase_cb=phase_cb,
                route=route,
                root_task_id=root_task_id,
            ) is not None

    def reset_chat_session(self) -> None:
        self._next_seed_thread_id = None
        self.last_thread_id = None

    def _manager_reply_runtime_context(self, run_label: str) -> str:
        workspace_context = ""
        try:
            from ..roles.prompts.manager import manager_workspace_capability_prompt

            configured_workspace = str(
                getattr(self._args, "operator_workspace", "")
                or getattr(self._args, "workdir", "")
                or ""
            ).strip()
            workspace = (
                Path(configured_workspace).expanduser()
                if configured_workspace
                else Path.cwd()
            )
            state_root = (
                Path(self._manager_session_root).expanduser()
                if getattr(self, "_manager_session_root", None)
                else workspace
            )
            workspace_context = manager_workspace_capability_prompt(
                workspace,
                manifest_root=state_root,
            )
        except Exception:  # noqa: BLE001 — context must never block a reply
            workspace_context = ""
        try:
            runner = getattr(self._backend, "_runner", None)
            if runner is None or not runner._acp_enabled(run_label):
                return workspace_context
        except Exception:  # noqa: BLE001 - metadata must never block a reply
            return workspace_context
        runtime_fact = (
            "Runtime fact (answer accurately if the operator asks): this "
            "operator-facing Manager conversation is one logical session on a "
            "long-lived Copilot ACP process. Ordinary turns use session/prompt "
            "on that same live process and session; they do NOT spawn a fresh "
            "CLI process with --resume, and Argus does NOT resend the full chat "
            "transcript each turn. The front-door classifier is isolated from "
            "this conversation, and the background task daemon is a separate "
            "process. A deliberate context rotation starts a new conversation "
            "session with a structured handoff."
        )
        return "\n\n".join(
            part for part in (workspace_context, runtime_fact) if part
        )

    def _live_mission_status_block(self) -> str:
        session_root = getattr(self, "_manager_session_root", None)
        if not session_root:
            return ""
        try:
            from ..cli.roles_status import role_activity
            from ..life.memory import Backlog

            root = Path(session_root)
            running = [
                item
                for item in Backlog(root / "backlog.jsonl").all()
                if item.status == "running"
            ]
            if not running:
                mission = self._recent_mission_history_block(root)
            else:
                item = running[0]
                activity = role_activity(root)
                lines = [
                    "## Live mission status",
                    "A mission is currently running under your supervision in a "
                    f"separate daemon process (life_dir={root}):",
                    f'- item: "{(item.title or "").strip()[:120]}" (id={item.id})',
                ]
                started = getattr(item, "started_ts", None)
                if isinstance(started, (int, float)) and started > 0:
                    lines[-1] += (
                        f", running for {max(0, int(time.time() - started))}s"
                    )
                for role in ("planner", "engineer", "reviewer"):
                    role_state = activity.get(role)
                    if role_state is None or role_state.status == "idle":
                        continue
                    lines.append(
                        f"- {role}: {role_state.label} ({role_state.status})"
                    )
                lines.extend([
                    "",
                    "Verify progress yourself before answering if useful — you have "
                    f"shell access and Manager authority over state under {root}.",
                    "Operator steering and abort requests are durable control actions. "
                    "Never say you are read-only or unable to direct the team.",
                ])
                mission = "\n".join(lines)
            maintenance = self._self_maintenance_status_block(root)
            return "\n\n".join(
                block for block in (mission, maintenance) if block
            )
        except Exception:  # noqa: BLE001 - status context is optional
            return ""

    @staticmethod
    def _self_maintenance_status_block(root: Path) -> str:
        from ..daemon.self_maintenance import read_self_maintenance_snapshot

        snapshot = read_self_maintenance_snapshot(root)
        if snapshot is None:
            return ""
        if snapshot.maintenance_available is True:
            isolation = "available"
        elif snapshot.maintenance_available is False:
            isolation = "unavailable"
        else:
            isolation = "unknown"
        phase = snapshot.phase or (
            "ready" if snapshot.maintenance_available is True else "idle"
        )
        lines = [
            "## Manager self-maintenance state",
            f"- phase: {phase}",
            f"- isolated repair capability: {isolation}",
        ]
        if snapshot.last_audit_at > 0:
            lines.append(
                "- last audit: "
                f"{max(0, int(time.time() - snapshot.last_audit_at))}s ago"
            )
        if snapshot.pr_url:
            lines.append(f"- open maintenance PR: {snapshot.pr_url}")
        if snapshot.publication_status:
            lines.append(f"- upstream publication: {snapshot.publication_status}")
        if snapshot.publication_error:
            lines.append(f"- publication note: {snapshot.publication_error}")
        return "\n".join(lines)

    def _recent_mission_history_block(self, root: Path) -> str:
        try:
            from ..life.memory import EventJournal

            recent = EventJournal(root / "events.jsonl").tail(1)
            if not recent:
                return ""
            entry = recent[0]
            age_s = max(0, int(time.time() - float(entry.ts)))
            lines = [
                "## Recent mission history",
                "No mission is running right now under your supervision "
                f"(life_dir={root}). The most recent recorded event there, "
                f"{age_s}s ago:",
                f'- {entry.kind}: "{(entry.title or "").strip()[:120]}"',
            ]
            summary = (entry.summary or "").strip()
            if summary:
                lines.append(f"  {summary[:300]}")
            lines.extend([
                "",
                "This may or may not be what the operator is asking about — judge "
                "relevance from its age and content. Verify yourself if useful "
                f"(grep logs, read files); you have real shell access under {root}.",
            ])
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - history context is optional
            return ""

    def _simple_quick_reply(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> _Outcome:
        from ..cli.roles_status import runner_backend_label
        from ..roles.prompts.manager import build_simple_prompt

        args = self._args
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        backend_label = runner_backend_label()
        sink.handle_event({
            "type": "loop.start",
            "text": f"SELF: one {backend_label} handling {objective[:120]}",
        })

        self._current_sink = sink
        self._current_failure_ledger = None
        prompt = build_simple_prompt(
            objective=objective,
            identity_card=self.manager.role_context(),
            mission_status=self._live_mission_status_block(),
            runtime_context=self._manager_reply_runtime_context("simple-1"),
            operator_workspace=str(
                getattr(args, "operator_workspace", "") or ""
            ),
        )
        configured_workspace = str(
            getattr(args, "operator_workspace", "") or ""
        ).strip()
        workdir = (
            Path(configured_workspace).expanduser()
            if configured_workspace
            else Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )

        def _self_inactivity(snapshot: Any) -> str | None:
            try:
                idle = int(getattr(snapshot, "idle_seconds", 0) or 0)
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "codex_idle",
                    "text": f"{backend_label} process running; no stream output for {idle}s",
                })
            except Exception:  # noqa: BLE001
                pass
            return None

        reply_message_id = f"manager-reply-{id(sink):x}"

        def _emit_block(block: str) -> None:
            body = (block or "").strip()
            if not body:
                return
            try:
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "assistant_message",
                    "agent_layer": "manager",
                    "message_id": reply_message_id,
                    "text": body,
                })
            except Exception:  # noqa: BLE001 - UI sinks never own the turn
                pass

        reply_model = resolve_manager_reply_model()
        options = RunnerOptions(
            model=reply_model,
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_SELF_REASONING_EFFORT",
                default="xhigh",
            ),
            full_auto=True,
            skip_git_repo_check=True,
            dangerous_yolo=True,
            working_dir=str(workdir),
            watchdog_hard_idle_seconds=env_int(
                "ARGUS_SKILL_SELF_HARD_IDLE_SECONDS", 120
            ),
            watchdog_soft_idle_seconds=env_int(
                "ARGUS_SKILL_SELF_SOFT_IDLE_SECONDS", 5
            ),
            inactivity_callback=_self_inactivity,
            on_agent_message=_emit_block,
        )
        attempt_results: list[Any] = []
        try:
            result = gateway_run_exec(
                self._backend,
                prompt=prompt,
                options=options,
                run_label="simple-1",
                resume_thread_id=seed,
            )
            attempt_results.append(result)
            if self_retryable_transport_failure(result):
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "provider_retry",
                    "agent_layer": "manager",
                    "text": (
                        "Copilot reply transport stalled; retrying once in a fresh session"
                    ),
                })
                result = gateway_run_exec(
                    self._backend,
                    prompt=prompt,
                    options=options,
                    run_label="simple-1",
                    resume_thread_id=None,
                )
                attempt_results.append(result)
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        fatal = getattr(result, "fatal_error", None)
        success = result.exit_code == 0 and not fatal and bool(last_msg)
        new_thread_id = getattr(result, "thread_id", None)
        round_thread_id = new_thread_id or seed
        result_status = "done" if success else "error"
        if should_clear_thread_id_after_outcome(
            status=result_status,
            fatal_error=str(getattr(result, "fatal_error", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_thread_id = None
        elif new_thread_id:
            self.last_thread_id = new_thread_id
            self._next_seed_thread_id = new_thread_id

        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "exit_code": int(getattr(result, "exit_code", 0) or 0),
            "input_tokens": sum(
                int(getattr(attempt, "input_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "cached_input_tokens": sum(
                int(getattr(attempt, "cached_input_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "output_tokens": sum(
                int(getattr(attempt, "output_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "reasoning_output_tokens": sum(
                int(getattr(attempt, "reasoning_output_tokens", 0) or 0)
                for attempt in attempt_results
            ),
            "premium_requests": sum(
                float(getattr(attempt, "premium_requests", 0.0) or 0.0)
                for attempt in attempt_results
            ),
            "model": str(
                getattr(result, "usage_model", "") or reply_model or ""
            ),
            "usage_scope": "delta",
            "last_message": last_msg,
            "session_id": round_thread_id,
            "turn_completed": bool(
                success
            ),
            "attempt_count": len(attempt_results),
        })

        status = "done" if success else "error"
        stop_reason = (
            ""
            if success
            else str(
                fatal
                or (
                    "Manager SELF turn completed without an assistant message"
                    if result.exit_code == 0
                    else f"exit={result.exit_code}"
                )
            )
        )
        auth_failure = self._consume_auth_failure()
        sink.handle_event({
            "type": "loop.done",
            "text": f"status={status} rounds=1 (simple)",
        })
        return _Outcome(
            success=success,
            status=status,
            stop_reason=stop_reason,
            rounds=1,
            last_thread_id=new_thread_id,
            chat_mode=False,
            auth_failure=auth_failure,
        )


__all__ = ["SelfReplyMixin", "self_retryable_transport_failure"]
