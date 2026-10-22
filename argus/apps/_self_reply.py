"""Foreground Manager routing and bounded SELF reply execution."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.knobs import (
    resolve_role_model,
    resolve_role_reasoning_effort,
)
from ..core.mission_view import say, uses_cjk
from ..core.models import RunnerOptions
from ..core.ports import EventSink
from ..core.progress_step import (
    REPLY_KINDS,
    ProgressDeduper,
    ProgressTally,
    describe_progress_step,
)

# Structured fields of one observable agent action that the cockpit can use
# beyond the rendered label: which tool, which call, and how it ended.
_PHASE_META_KEYS = ("tool_name", "call_id", "tool_kind", "status", "exit_code", "output_excerpt")
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import known_secret_values, redact_secrets_record
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._env import env_flag, env_int
from ._runtime_backends import _Outcome

if TYPE_CHECKING:
    from ..manager import Manager

_SELF_RETRYABLE_TRANSPORT_ERRORS = (
    "upstream request failed: endpoint is unavailable",
    "provider finish_reason: network_error",
)
_SELF_EXECUTION_CONTRACTS = {
    "micro": (
        "bash",
        "Complete the finite local microtask in the current directory. Inspect the relevant "
        "local inputs in tool output before drafting or extracting facts; do not guess their contents "
        "or format. Then make the requested change and verify it. Use only necessary "
        "tool calls, report briefly, and stop.",
        "high",
    ),
    "implement": (
        "read,bash,edit,write",
        "Complete this finite local implementation task. Inspect the relevant starter "
        "together, design one coherent change, implement all required files, run focused "
        "end-to-end checks once, fix only observed failures, report briefly, and stop.",
        "high",
    ),
    "debug": (
        "read,bash,edit,write",
        "Complete this finite local debugging task. Inspect the implementation and visible "
        "tests together, identify concrete invariant violations, make one coherent targeted "
        "edit, run focused tests once, add only requested regression coverage, report "
        "briefly, and stop.",
        "medium",
    ),
    "review": (
        "read,bash,write",
        "Complete this finite local code review. Read the target with line numbers once, "
        "trace only concrete observable correctness or authorization failures, write "
        "exactly the requested review artifact without modifying source, validate its "
        "format once, report briefly, and stop.",
        "medium",
    ),
    "synthesize": (
        "read,bash,write",
        "Complete this finite supplied-source synthesis. Read all named local sources "
        "together, separate quoted measurements from interpretation, draft exactly the "
        "requested artifact with citations, validate its constraints once, report briefly, "
        "and stop. Do not use outside knowledge.",
        "medium",
    ),
}


def _redact_live_event(event: dict[str, Any]) -> dict[str, Any]:
    safe = redact_secrets_record(event, known_values=known_secret_values())
    return safe if isinstance(safe, dict) else event


def self_retryable_transport_failure(result: Any) -> bool:
    """Retry one empty transport failure with no possible side effects."""
    if (getattr(result, "last_agent_message", "") or "").strip():
        return False
    if bool(getattr(result, "tool_activity_observed", False)):
        return False
    fatal = str(getattr(result, "fatal_error", "") or "").strip().casefold()
    if not fatal:
        return int(getattr(result, "exit_code", 0) or 0) == 0
    if fatal.startswith(("external interrupt:", "refused before start:")):
        return False
    return any(marker in fatal for marker in _SELF_RETRYABLE_TRANSPORT_ERRORS)


_SELF_STALL_MARKERS = ("forced restart after",)


def self_stalled_model_turn(result: Any) -> bool:
    """The watchdog killed a turn because the provider stopped streaming.

    Unlike a transport failure this can follow tool calls, so the retry must
    continue the same provider thread (their results are already in it)
    rather than start over and redo them.
    """
    if (getattr(result, "last_agent_message", "") or "").strip():
        return False
    fatal = str(getattr(result, "fatal_error", "") or "").strip().casefold()
    return bool(fatal) and any(marker in fatal for marker in _SELF_STALL_MARKERS)


_SELF_STALL_CONTINUATION = (
    "Your previous attempt stalled before answering: the model produced no output "
    "for a while and the turn was cut off. Continue from where you left off and give "
    "the operator the final answer now. Do not repeat tool calls whose results are "
    "already in this conversation."
)


def _execution_without_tools(result: Any) -> bool:
    """A successful prose turn is not evidence that a local action ran."""
    return (
        getattr(result, "tool_activity_observed", None) is False
        and int(getattr(result, "exit_code", 0) or 0) == 0
        and not getattr(result, "fatal_error", None)
        and bool(str(getattr(result, "last_agent_message", "") or "").strip())
    )


class SelfReplyMixin:
    """Operator-facing Manager front door mixed into ``_SkillLoopRunner``."""

    manager: Manager
    _args: argparse.Namespace

    def _maybe_chat_outcome(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        self_mode: str = "inspect",
        skill_vertical: str | None = None,
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

        def _phase(
            label: str,
            *,
            role: str = "manager",
            kind: str = "",
            detail: str = "",
            meta: dict[str, Any] | None = None,
        ) -> None:
            if not callable(phase_cb):
                return
            for kwargs in (
                {"role": role, "kind": kind, "detail": detail, "meta": meta},
                {"role": role, "kind": kind, "detail": detail},
                {"role": role},
                {},
            ):
                try:
                    phase_cb(label, **kwargs)
                    return
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001 - UI callbacks never own the turn
                    return

        # Progress events do not say where the agent works; files outside this
        # root are Argus' own plumbing and are labelled as such.
        progress_workspace = str(
            Path(str(getattr(self._args, "operator_workspace", "") or "")).expanduser()
            if getattr(self._args, "operator_workspace", "")
            else workdir
        )

        class _PhaseSink:
            def __init__(self, inner: EventSink) -> None:
                self._inner = inner
                self._dedupe = ProgressDeduper()
                self.tally = ProgressTally()

            def handle_event(self, event: dict[str, Any]) -> None:
                safe_event = _redact_live_event(event)
                event_type = str(safe_event.get("type") or "")
                kind = str(safe_event.get("kind") or "")
                is_reply = event_type == "engineer.progress" and kind in REPLY_KINDS
                if event_type == "loop.start":
                    _phase(
                        "Argus 正在处理你的消息…",
                        kind="loop.start",
                    )
                elif event_type == "engineer.progress" and not is_reply:
                    label, detail = describe_progress_step(safe_event, progress_workspace)
                    self.tally.observe(safe_event)
                    phase_kind = self._dedupe.phase_kind(safe_event, label)
                    if not phase_kind:
                        self._inner.handle_event(safe_event)
                        return
                    meta = {
                        key: safe_event[key]
                        for key in _PHASE_META_KEYS
                        if safe_event.get(key) not in (None, "")
                    }
                    _phase(label, kind=phase_kind, detail=detail, meta=meta or None)
                self._inner.handle_event(safe_event)

            def handle_stream_line(self, stream: str, line: str) -> None:
                handler = getattr(self._inner, "handle_stream_line", None)
                if callable(handler):
                    handler(stream, line)

            def close(self) -> None:
                closer = getattr(self._inner, "close", None)
                if callable(closer):
                    closer()

        # Plain words for the operator: the backend's name is a detail of how
        # Argus works, not something a reader has to know.
        _phase("正在理解你的要求…")
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
            mode = str(self_mode or "inspect").strip().lower()
            from ..daemon.runtime_status import (
                STATUS_MODES,
                observe_runtime_status,
                render_runtime_status,
            )

            if mode in STATUS_MODES:
                _phase("正在读取运行状态…")
                root = self._manager_state_path()
                facts = observe_runtime_status(root, mode)
                reply = render_runtime_status(facts, chinese=uses_cjk(objective))
                self._last_self_mode = mode
                sink.handle_event({
                    "type": "round.main.completed", "round_index": 1,
                    "exit_code": 0, "turn_completed": True, "last_message": reply,
                    "input_tokens": 0, "output_tokens": 0, "usage_scope": "delta",
                })
                return _Outcome(success=True, status="done", rounds=0, chat_mode=True)
            _phase("正在开始处理这项工作…")
            return self._simple_quick_reply(
                objective=objective,
                sink=_PhaseSink(sink),
                seed_thread_id=seed_thread_id,
                lean=mode == "reply",
                execute_mode=mode if mode in _SELF_EXECUTION_CONTRACTS else "",
                root_task_id=root_task_id,
                skill_vertical=skill_vertical,
            )
        _phase("正在安排接下来要做的事…")
        return None


    def chat_reply_if_conversational(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
        phase_cb: Any = None,
        route: str | None = None,
        self_mode: str = "inspect",
        skill_vertical: str | None = None,
        root_task_id: str | None = None,
    ) -> bool:
        with self.task_usage_context(root_task_id):
            outcome = self._maybe_chat_outcome(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
                phase_cb=phase_cb,
                route=route,
                self_mode=self_mode,
                skill_vertical=skill_vertical,
                root_task_id=root_task_id,
            )
        self.last_chat_outcome = outcome
        return outcome is not None

    def reset_chat_session(self) -> None:
        self._next_seed_thread_id = None
        self.last_thread_id = None

    def _manager_state_path(self) -> Path:
        return Path(getattr(self, "_manager_session_root", None) or self.manager.manager_session_root)

    def _manager_reply_runtime_context(self) -> str:
        workspace_context = ""
        team_log_context = ""
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
            if getattr(self, "_manager_session_root", None):
                team_log = state_root / "events.jsonl"
                team_log_context = (
                    f"Authoritative Team log: {team_log}\n"
                    "Team replies are not automatically copied into this chat. "
                    "When the operator asks about Team work, read that log yourself "
                    "before answering."
                )
        except Exception:  # noqa: BLE001 — context must never block a reply
            workspace_context = ""
            team_log_context = ""
        return "\n\n".join(
            part for part in (workspace_context, team_log_context) if part
        )

    def _live_mission_status_block(self) -> str:
        session_root = getattr(self, "_manager_session_root", None)
        if not session_root:
            return ""
        try:
            from ..daemon.life_worker import read_daemon_status
            from ..life.memory import Backlog
            from ..life.role_activity import role_activity

            root = Path(session_root)
            daemon = read_daemon_status(root)
            daemon_lines = [
                "## Current project's background worker",
                f"- alive: {'true' if daemon.alive else 'false'}",
                f"- pid: {daemon.pid if daemon.alive and daemon.pid is not None else 'none'}",
                (
                    "- evidence source: daemon status/pid files for this session; "
                    "this is not WebAPI or host liveness. No worker PID may simply mean "
                    "this project has never started background work. Foreground requests "
                    "and other projects can still be running."
                ),
            ]
            daemon_block = "\n".join(daemon_lines)
            backlog_items = Backlog(root / "backlog.jsonl").all()
            running = [
                item
                for item in backlog_items
                if item.status == "running"
            ]
            if not running:
                mission = self._recent_mission_history_block(root, backlog_items)
            else:
                item = running[0]
                activity = role_activity(root)
                lines = (
                    [
                        "## Live mission status",
                        "A mission is currently running under your supervision in a "
                        f"separate daemon process (life_dir={root}):",
                    ]
                    if daemon.alive
                    else [
                        "## Interrupted mission status",
                        "The backlog still marks a mission as running, but the "
                        "authoritative daemon status is offline:",
                    ]
                )
                lines.append(
                    f'- item: "{(item.title or "").strip()[:120]}" (id={item.id})'
                )
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
            from ..manager.observation import observe_project

            evidence = observe_project(root).render()
            return "\n\n".join(block for block in (daemon_block, mission, evidence) if block)
        except Exception:  # noqa: BLE001 - status context is optional
            return ""

    def _recent_mission_history_block(
        self,
        root: Path,
        backlog_items: list[Any] | None = None,
    ) -> str:
        try:
            from ..life.memory import EventJournal

            recent = EventJournal(root / "events.jsonl").tail(1)
            latest_item = backlog_items[-1] if backlog_items else None
            if not recent and latest_item is None:
                return ""
            lines = [
                "## Recent mission history",
                "No mission is running right now under your supervision "
                f"(life_dir={root}).",
            ]
            if latest_item is not None:
                lines.append(
                    "- latest backlog task: "
                    f"[{latest_item.status}] "
                    f'"{(latest_item.title or "").strip()[:120]}" '
                    f"(id={latest_item.id})"
                )
                objective = " ".join(
                    str(
                        latest_item.original_objective
                        or latest_item.objective
                        or ""
                    ).split()
                )
                if objective:
                    lines.append(f"  operator objective: {objective[:600]}")
            if recent:
                entry = recent[0]
                age_s = max(0, int(time.time() - float(entry.ts)))
                lines.append(
                    f'- latest recorded event ({age_s}s ago): {entry.kind}: '
                    f'"{(entry.title or "").strip()[:120]}"'
                )
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
        lean: bool = False,
        execute_mode: str = "",
        skill_vertical: str | None = None,
        root_task_id: str | None = None,
    ) -> _Outcome:
        from ..core.role_config import runner_backend_label
        from ..roles.prompts.manager import (
            build_quick_reply_prompt,
            build_simple_prompt,
        )

        args = self._args
        # A matched task needs tools to read its Skills even if the classifier
        # chose a prose-only answer. Greetings without a match stay lean.
        lean = lean and not skill_vertical
        execution_contract = _SELF_EXECUTION_CONTRACTS.get(execute_mode)
        executing = execution_contract is not None
        seed = (
            None
            if lean or executing
            else self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        )
        self._last_self_mode = (
            "reply" if lean else execute_mode if executing else "inspect"
        )
        backend_label = runner_backend_label()
        sink.handle_event({
            "type": "loop.start",
            "text": f"SELF: one {backend_label} handling {objective[:120]}",
        })

        self._current_sink = sink
        self._current_failure_ledger = None
        configured_workspace = str(
            getattr(args, "operator_workspace", "") or ""
        ).strip()
        workdir = (
            Path(configured_workspace).expanduser()
            if configured_workspace
            else Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        from ..skills.role_library import RoleSkillLibraries

        libraries = RoleSkillLibraries(role="self")
        if not lean:
            from ..manager.self_context import self_skill_context

            libraries = self_skill_context(
                self.manager, vertical=skill_vertical, task=objective,
                role="reviewer" if execute_mode == "review" else "engineer",
                on_event=sink.handle_event,
            )
        memory = getattr(args, "manager_memory", None)
        if lean:
            prompt = build_quick_reply_prompt(objective=objective)
            from ..manager.observation import observe_project

            state_root = self._manager_state_path()
            prompt += "\n\n" + observe_project(state_root).render()
            add_dirs: list[str] = []
            native_skill_paths: list[str] = []
        elif execution_contract is not None:
            prompt = (
                objective.strip()
                if str(getattr(self._backend, "backend", "")) == "pi"
                else f"{execution_contract[1]}\n\nTask:\n{objective.strip()}"
            )
            # Finite work keeps its compact prompt, not at the cost of what
            # Argus already knows about it.
            recalled = memory.render_recall_context(
                objective, role="manager", mission_id=root_task_id or "",
            ) if memory is not None else ""
            prompt = "\n\n".join(block for block in (libraries.block, recalled, prompt) if block)
            add_dirs = [str(path) for path in libraries.library_roots]
            native_skill_paths = [str(path) for path in libraries.native_paths]
        else:
            memory_prelude = (
                memory.render_prelude(
                    objective=objective, role="manager", mission_id=root_task_id or "",
                )
                if memory is not None
                else ""
            )
            prompt = build_simple_prompt(
                objective=objective,
                identity_card=memory_prelude,
                skill_library=libraries.block,
                mission_status=self._live_mission_status_block(),
                runtime_context=self._manager_reply_runtime_context(),
                operator_workspace=str(workdir),
            )
            native_skill_paths = [str(path) for path in libraries.native_paths]
            session_root = getattr(self, "_manager_session_root", None)
            add_dirs = [
                *([str(Path(session_root).expanduser())]
                  if session_root and Path(session_root).expanduser() != workdir else []),
                *(str(path) for path in libraries.library_roots),
            ]
        if not lean:
            from ..life.knowledge_recall import knowledge_read_dirs

            # Recall points at libraries outside the workspace; a sandboxed
            # worker reads them only when they are given.
            add_dirs += knowledge_read_dirs(
                getattr(memory, "project_root", None), getattr(memory, "global_root", None), workdir,
            )
            from ..roles.prompts.manager import build_self_learning_note

            learning_note = build_self_learning_note(workdir)
            if learning_note:
                prompt += "\n\n" + learning_note

        from ..core.operator_context import (
            append_operator_context,
            build_operator_context_block,
        )

        operator_context, operator_context_revision = build_operator_context_block(
            "manager",
            getattr(self, "_manager_session_root", None),
            consume_once=False,
        )
        prompt = append_operator_context(prompt, operator_context)

        def _self_inactivity(snapshot: Any) -> str | None:
            try:
                idle = int(getattr(snapshot, "idle_seconds", 0) or 0)
                tally = getattr(sink, "tally", None)
                text = (
                    f"仍在进行：{tally.summary()}"
                    if isinstance(tally, ProgressTally)
                    else f"仍在进行，已有 {idle} 秒没有新的输出…"
                )
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "codex_idle",
                    "text": text,
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

        effective_backend = getattr(self._args, "backend", None)
        reply_model = (
            str(getattr(args, "engineer_model", "") or "")
            if executing
            else resolve_role_model(
                "manager", role_env="ARGUS_SKILL_MANAGER_MODEL", backend=effective_backend,
            )
        )
        reply_effort = (
            "low"
            if lean
            else resolve_role_reasoning_effort(
                "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT",
                default=execution_contract[2],
            )
            if executing
            else resolve_role_reasoning_effort(
                "ARGUS_SKILL_SELF_REASONING_EFFORT",
                default="high",
            )
        )
        run_label = (
            "manager-quick-reply"
            if lean
            else f"self-{execute_mode}"
            if executing
            else "simple-1"
        )
        extra_args = (
            [
                "--tools",
                execution_contract[0],
                "--system-prompt",
                execution_contract[1],
            ]
            if execution_contract is not None
            and str(getattr(self._backend, "backend", "")) == "pi"
            else None
        )
        options = RunnerOptions(
            model=reply_model,
            reasoning_effort=reply_effort,
            full_auto=False,
            skip_git_repo_check=True,
            dangerous_yolo=not lean,
            sandbox_mode="read-only" if lean else None,
            force_safe_mode=lean,
            disable_tools=lean,
            working_dir=str(workdir),
            add_dirs=add_dirs,
            skill_paths=native_skill_paths,
            extra_args=extra_args,
            watchdog_soft_idle_seconds=env_int(
                "ARGUS_SKILL_SELF_SOFT_IDLE_SECONDS", 5
            ),
            inactivity_callback=_self_inactivity,
            on_agent_message=_emit_block,
        )
        attempt_results: list[Any] = []
        from ..manager.session_context import conversation_backend

        backend = self._backend if executing else conversation_backend(self)
        try:
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=options,
                run_label=run_label,
                resume_thread_id=seed,
            )
            attempt_results.append(result)
            no_action = executing and _execution_without_tools(result)
            stalled = self_stalled_model_turn(result)
            stall_resume = (
                (getattr(result, "thread_id", None) or seed) if stalled else None
            )
            if stalled and not stall_resume and bool(
                getattr(result, "tool_activity_observed", False)
            ):
                # Tools already ran and there is no thread to continue: a blind
                # restart would redo them, so surface the stall instead.
                stalled = False
            if self_retryable_transport_failure(result) or no_action or stalled:
                sink.handle_event({
                    "type": "engineer.progress",
                    "kind": "provider_retry",
                    "agent_layer": "manager",
                    "text": say(
                        "retry_no_action" if no_action
                        else "retry_resumed" if stalled and stall_resume
                        else "retry_stalled" if stalled
                        else "retry_dropped",
                        uses_cjk(objective),
                    ),
                })
                result = gateway_run_exec(
                    backend,
                    prompt=(
                        prompt + "\n\nYour previous turn ended with prose and no tool calls. "
                        "Perform the requested local work now using the available tools. "
                        "Inspect the inputs, create or update the requested files, verify them, "
                        "then report the actual result. Do not return another plan."
                        if no_action else
                        _SELF_STALL_CONTINUATION if stall_resume else prompt
                    ),
                    options=options,
                    run_label=run_label,
                    resume_thread_id=stall_resume,
                )
                attempt_results.append(result)
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        fatal = getattr(result, "fatal_error", None)
        if executing and _execution_without_tools(result):
            fatal = "The execution turn returned text without performing any tool action."
        success = result.exit_code == 0 and not fatal and bool(last_msg)
        new_thread_id = getattr(result, "thread_id", None)
        round_thread_id = new_thread_id or seed
        if should_clear_thread_id_after_outcome(
            fatal_error=str(getattr(result, "fatal_error", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_thread_id = None
        elif new_thread_id and not executing:
            self.last_thread_id = new_thread_id
            self._next_seed_thread_id = new_thread_id
        elif executing:
            new_thread_id = None

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
            "operator_context_revision": operator_context_revision,
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
        delivery = None
        # Execution turns and tool-using research/inspection turns both deliver
        # the files they created and named in the reply. The model decides
        # whether an answer deserves a kept file; a plain chat reply has none.
        if success and not lean:
            from ..core.secret_guard import known_secret_values, redact_secrets_text
            from ..life.delivery import (
                build_delivery_receipt,
                referenced_delivery_paths,
            )

            safe_last_msg = redact_secrets_text(
                last_msg,
                known_values=known_secret_values(),
            )
            started_at = float(getattr(result, "started_at", 0.0) or 0.0)
            paths = [
                path
                for path in referenced_delivery_paths(workdir, [safe_last_msg])
                if started_at > 0
                and (workdir / path).stat().st_mtime >= started_at
            ]
            if paths:
                delivery = build_delivery_receipt(
                    item_id=str(
                        getattr(result, "call_id", "")
                        or root_task_id
                        or reply_message_id
                    ),
                    title=Path(paths[0]).name,
                    summary=safe_last_msg,
                    success=True,
                    overall_complete=True,
                    status="done",
                    review_status="not_assessed",
                    review_source="solo_output",
                    final_submission_certified=False,
                    workspace=workdir,
                    state_root=(
                        Path(self._manager_session_root)
                        if getattr(self, "_manager_session_root", None)
                        else workdir
                    ),
                    reviewer_artifacts=paths,
                    artifact_source="solo_output",
                )
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
            delivery=delivery,
        )


__all__ = [
    "SelfReplyMixin",
    "self_retryable_transport_failure",
    "self_stalled_model_turn",
]
