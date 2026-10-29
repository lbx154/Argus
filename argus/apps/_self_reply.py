"""The Manager's conversation: one persistent session answers every operator message."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.knobs import env_int, resolve_role_model
from ..core.models import RunnerOptions
from ..core.ports import EventSink
from ..core.progress_step import REPLY_KINDS, ProgressDeduper, ProgressTally, describe_progress_step
from ..core.role_actions import role_actions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import known_secret_values, redact_secrets_record
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._runtime_backends import _Outcome

if TYPE_CHECKING:
    from ..core.handoff import HandOffActions
    from ..manager import Manager

# Structured fields of one observable agent action that the cockpit can use
# beyond the rendered label: which tool, which call, and how it ended.
_PHASE_META_KEYS = ("tool_name", "call_id", "tool_kind", "status", "exit_code", "output_excerpt")


def _redact_live_event(event: dict[str, Any]) -> dict[str, Any]:
    safe = redact_secrets_record(event, known_values=known_secret_values())
    return safe if isinstance(safe, dict) else event


class _PhaseSink:
    """Relays the turn's events, and names each visible step for the operator's live status."""

    def __init__(self, inner: EventSink, phase: Any, workspace: str) -> None:
        self._inner = inner
        self._phase = phase
        self._workspace = workspace
        self._dedupe = ProgressDeduper()
        self.tally = ProgressTally()

    def handle_event(self, event: dict[str, Any]) -> None:
        event = _redact_live_event(event)
        if event.get("type") == "engineer.progress" and event.get("kind") not in REPLY_KINDS:
            label, detail = describe_progress_step(event, self._workspace)
            self.tally.observe(event)
            kind = self._dedupe.phase_kind(event, label)
            if kind and callable(self._phase):
                meta = {key: event[key] for key in _PHASE_META_KEYS if event.get(key) not in (None, "")}
                self._phase(label, kind=kind, detail=detail, meta=meta or None)
        self._inner.handle_event(event)


class SelfReplyMixin:
    """The Manager's conversation, mixed into ``_SkillLoopRunner``."""

    manager: Manager
    _args: argparse.Namespace

    def manager_turn(
        self,
        *,
        message: str,
        sink: EventSink,
        actions: HandOffActions,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        root_task_id: str | None = None,
        deciding: bool = False,
    ) -> _Outcome:
        """One operator message, one turn of the Manager's session; ``actions`` holds what it handed off.

        ``deciding`` marks a turn that rules on a team stage question; it
        thinks harder than an ordinary chat turn (``core.effort_policy``).
        """
        with self.task_usage_context(root_task_id):
            outcome = self._manager_turn(
                message=message, sink=sink, actions=actions, seed_thread_id=seed_thread_id,
                phase_cb=phase_cb, root_task_id=root_task_id, deciding=deciding,
            )
        self.last_chat_outcome = outcome
        return outcome

    def reset_chat_session(self) -> None:
        self._next_seed_thread_id = None
        self.last_thread_id = None

    def _manager_state_path(self) -> Path:
        return Path(getattr(self, "_manager_session_root", None) or self.manager.manager_session_root)

    def _manager_turn(
        self,
        *,
        message: str,
        sink: EventSink,
        actions: HandOffActions,
        seed_thread_id: str | None,
        phase_cb: Any,
        root_task_id: str | None,
        deciding: bool = False,
    ) -> _Outcome:
        from ..core.effort_policy import manager_chat_effort
        from ..core.operator_context import append_operator_context, build_operator_context_block
        from ..life.knowledge_recall import knowledge_read_dirs
        from ..manager.self_context import self_skill_context
        from ..manager.session_context import conversation_backend
        from ..roles.prompts.manager import build_manager_prompt

        args = self._args
        workdir = Path(
            getattr(args, "operator_workspace", "") or getattr(args, "workdir", "") or Path.cwd()
        ).expanduser()
        state_root = self._manager_state_path()
        sink = _PhaseSink(sink, phase_cb, str(workdir))
        libraries = self_skill_context(self.manager, task=message, on_event=sink.handle_event)
        # The page goes once per conversation; later turns are the message and
        # the Skill libraries block, which the run gateway drops while the
        # thread already holds it verbatim. After an upgrade moves the
        # libraries, the thread's paths are stale and the new block goes again
        # (2026-10-26: a Manager turn searched two release trees for its Skills).
        # The block comes after the page so its sections are byte-identical in
        # both prompts (the page has no heading of its own to end them).
        prompt = "\n\n".join(block for block in (message.strip(), libraries.block) if block) \
            if seed_thread_id else "\n\n".join(
                block for block in (build_manager_prompt(
                    message, workspace=workdir, state_root=state_root,
                ), libraries.block) if block
            )
        operator_context, operator_context_revision = build_operator_context_block(
            "manager", getattr(self, "_manager_session_root", None), consume_once=False,
        )
        prompt = append_operator_context(prompt, operator_context)
        write_dirs = [str(state_root)] if state_root != workdir else []
        # The workspace before the turn: its receipt lists what the turn changed.
        before = self._workspace_facts(workdir, state_root)
        memory = getattr(args, "manager_memory", None)
        reply_message_id = f"manager-reply-{id(sink):x}"

        def _emit_block(block: str) -> None:
            if block.strip():
                sink.handle_event({
                    "type": "engineer.progress", "kind": "assistant_message", "agent_layer": "manager",
                    "message_id": reply_message_id, "text": block.strip(),
                })

        def _still_working(_snapshot: Any) -> None:
            sink.handle_event({
                "type": "engineer.progress", "kind": "codex_idle", "text": f"仍在进行：{sink.tally.summary()}",
            })

        model = resolve_role_model(
            "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=getattr(args, "backend", None),
        )
        options = RunnerOptions(
            model=model,
            reasoning_effort=manager_chat_effort(deciding=deciding),
            skip_git_repo_check=True,
            dangerous_yolo=True,
            working_dir=str(workdir),
            add_dirs=[
                *write_dirs, *map(str, libraries.library_roots),
                *knowledge_read_dirs(
                    getattr(memory, "project_root", None), getattr(memory, "global_root", None), workdir,
                ),
            ],
            write_dirs=write_dirs,
            skill_paths=[str(path) for path in libraries.native_paths],
            watchdog_soft_idle_seconds=env_int("ARGUS_SKILL_SELF_SOFT_IDLE_SECONDS", 5),
            inactivity_callback=_still_working,
            on_agent_message=_emit_block,
        )
        backend = conversation_backend(self)
        sink.handle_event({"type": "loop.start", "text": f"Manager: {message[:120]}"})
        self._current_sink = sink
        try:
            with role_actions(backend, options, actions) as bound:
                result = gateway_run_exec(
                    backend, prompt=prompt, options=bound, run_label="simple-1", resume_thread_id=seed_thread_id,
                )
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        fatal = getattr(result, "fatal_error", None)
        success = result.exit_code == 0 and not fatal and bool(last_msg or actions.handoff)
        new_thread_id = getattr(result, "thread_id", None)
        if should_clear_thread_id_after_outcome(fatal_error=str(fatal or "")):
            self.last_thread_id = self._next_seed_thread_id = new_thread_id = None
        elif new_thread_id:
            self.last_thread_id = self._next_seed_thread_id = new_thread_id
        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "exit_code": int(result.exit_code or 0),
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(getattr(result, "cached_input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "reasoning_output_tokens": int(getattr(result, "reasoning_output_tokens", 0) or 0),
            "premium_requests": float(getattr(result, "premium_requests", 0.0) or 0.0),
            "model": str(getattr(result, "usage_model", "") or model or ""),
            "usage_scope": "delta",
            "last_message": last_msg,
            "session_id": new_thread_id or seed_thread_id,
            "turn_completed": success,
            "operator_context_revision": operator_context_revision,
        })
        status = "done" if success else "error"
        stop_reason = "" if success else str(
            fatal or ("Manager turn completed without an assistant message"
                      if result.exit_code == 0 else f"exit={result.exit_code}")
        )
        auth_failure = self._consume_auth_failure()
        delivery = self._delivery(
            result, last_msg, workdir, state_root, root_task_id or reply_message_id, before,
        ) if success else None
        sink.handle_event({"type": "loop.done", "status": status, "rounds": 1})
        return _Outcome(
            success=success, status=status, stop_reason=stop_reason, rounds=1,
            last_thread_id=new_thread_id, chat_mode=False, auth_failure=auth_failure, delivery=delivery,
        )

    @staticmethod
    def _workspace_facts(workdir: Path, state_root: Path) -> dict[str, Any]:
        """The workspace as ``workspace_facts``, leaving out the state kept inside it."""
        from ..core.workspace_facts import workspace_facts

        try:
            own = state_root.resolve().relative_to(workdir.resolve()).as_posix()
        except (OSError, ValueError):
            own = ""
        if own == ".":
            # The state is the workspace: its own writes would read as the work.
            return {"unavailable": True}
        return workspace_facts(workdir, ignore=[own] if own else [])

    @staticmethod
    def _delivery(
        result: Any, reply: str, workdir: Path, state_root: Path, item_id: str,
        before: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The files this turn changed, as one delivery; None when it changed none.

        With the workspace unknown before or after the turn, only the files
        its reply names and the turn wrote count.
        """
        from ..core.secret_guard import redact_secrets_text
        from ..life.delivery import (
            build_delivery_receipt,
            changed_workspace_paths,
            referenced_delivery_paths,
        )

        safe_reply = redact_secrets_text(reply, known_values=known_secret_values())
        started_at = float(getattr(result, "started_at", 0.0) or 0.0)
        named = [
            path for path in referenced_delivery_paths(workdir, [safe_reply])
            if started_at > 0 and (workdir / path).stat().st_mtime >= started_at
        ]
        changed = None if before is None else changed_workspace_paths(
            workdir, before, SelfReplyMixin._workspace_facts(workdir, state_root), named=named,
        )
        paths = named if changed is None else changed
        if not paths:
            return None
        return build_delivery_receipt(
            item_id=str(getattr(result, "call_id", "") or item_id),
            title=Path(paths[0]).name,
            summary=safe_reply,
            success=True,
            overall_complete=True,
            status="done",
            review_status="not_assessed",
            review_source="solo_output",
            final_submission_certified=False,
            workspace=workdir,
            state_root=state_root,
            reviewer_artifacts=paths,
            artifact_source="solo_output",
        )


__all__ = ["SelfReplyMixin"]
