from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from ..core.sandbox import sandboxed_child_env
from .models import AgentRunResult
from .runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    DEFAULT_RUNNER_BACKEND,
    RunnerBackend,
    default_runner_bin,
)

EventCallback = Callable[[str, str], None]
InactivityDecision = Literal["continue", "restart"]

# Manager run labels routed through the warm ``copilot --acp`` client (see
# ``_acp_enabled``).  The classifier and the operator-facing conversation use
# separate ACP sessions; each configured model gets one long-lived process.
# Mission roles remain on the ordinary one-shot CLI path. The set is overridable via
# ``ARGUS_SKILL_COPILOT_ACP_LABELS``.
_ACP_MANAGER_LABELS = frozenset(
    {
        "manager-frontdoor-classify",
        "simple-1",
        "chat-1",
    }
)

_READ_ONLY_FLAG_SWITCHES = frozenset({
    "--allow-all",
    "--allow-all-paths",
    "--allow-all-tools",
    "--allowed-tools",
    "--allowedTools",
    "--allow-tool",
    "--available-tools",
    "--autopilot",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--dangerously-skip-permissions",
    "--full-auto",
    "--permission-mode",
    "--sandbox",
    "--tools",
    "--yolo",
    "-C",
    "-s",
    "--add-dir",
    "--cd",
})
_READ_ONLY_VALUE_SWITCHES = frozenset({
    "--allow-tool",
    "--allowed-tools",
    "--allowedTools",
    "--available-tools",
    "--permission-mode",
    "--sandbox",
    "--tools",
    "-C",
    "-s",
    "--add-dir",
    "--cd",
})


def _read_only_extra_args(args: list[str], *, backend: RunnerBackend) -> list[str]:
    """Drop any extra argument capable of broadening a read-only Manager call."""
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        value = str(args[index] or "")
        config_switches = (
            {"-c", "--config"} if backend == BACKEND_CODEX else {"--config"}
        )
        if value in config_switches and index + 1 < len(args):
            payload = str(args[index + 1] or "")
            key = payload.partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 2
                continue
            cleaned.extend([value, payload])
            index += 2
            continue
        if value.startswith("--config="):
            key = value.partition("=")[2].partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 1
                continue
        if backend == BACKEND_CODEX and value.startswith("-c") and value != "-c":
            payload = value[2:].lstrip("=")
            key = payload.partition("=")[0].strip().casefold()
            if key.startswith((
                "approval", "permission", "sandbox", "shell_environment", "tools",
            )):
                index += 1
                continue
        if value.startswith(("-C", "-s")) and value not in {"-C", "-s"}:
            index += 1
            continue
        flag = value.partition("=")[0]
        if flag in _READ_ONLY_FLAG_SWITCHES:
            index += 2 if flag in _READ_ONLY_VALUE_SWITCHES and "=" not in value else 1
            continue
        cleaned.append(value)
        index += 1
    return cleaned


def _incomplete_turn_error(stderr_lines: list[str]) -> str:
    """Best available diagnostic for a CLI that exited without a model turn."""
    nonempty = [line.strip() for line in stderr_lines if line.strip()]
    for line in reversed(nonempty):
        if line.casefold().startswith(("error:", "fatal:")):
            return line
    if nonempty:
        return nonempty[-1]
    return "Agent CLI exited without completing a model turn."


@dataclass
class InactivitySnapshot:
    idle_seconds: float
    command: list[str]
    thread_id: str | None
    last_agent_message: str
    stdout_tail: list[str]
    stderr_tail: list[str]
    run_label: str | None = None


InactivityCallback = Callable[[InactivitySnapshot], InactivityDecision]
ExternalInterruptProvider = Callable[[], str | None]


@dataclass
class RunnerOptions:
    model: str | None = None
    reasoning_effort: str | None = None
    dangerous_yolo: bool = False
    full_auto: bool = False
    max_budget_usd: float | None = None
    max_ai_credits: int | None = None
    # Codex sandbox policy. When set (e.g. "workspace-write"), the codex command
    # is built with ``-s <mode> -C <working_dir> --add-dir <add_dirs>`` so writes
    # are confined to the workspace + add_dirs, and the child env is scrubbed of
    # push-capable VCS credentials with PYTHONSAFEPATH=1 — INSTEAD of
    # ``--dangerously-bypass-approvals-and-sandbox``. None = legacy behaviour
    # (dangerous_yolo / full_auto flags), so existing callers are unaffected.
    sandbox_mode: str | None = None
    skip_git_repo_check: bool = False
    # Enable codex's native live web_search tool (``-c web_search="live"``).
    live_search: bool = False
    extra_args: list[str] | None = None
    working_dir: str | None = None
    output_schema_path: str | None = None
    watchdog_soft_idle_seconds: int | None = None
    watchdog_hard_idle_seconds: int | None = None
    inactivity_callback: InactivityCallback | None = None
    external_interrupt_reason_provider: ExternalInterruptProvider | None = None
    add_dirs: list[str] | None = None
    plugin_dirs: list[str] | None = None
    file_specs: list[str] | None = None
    worktree_name: str | None = None
    # Fired with each NEW assistant message block the instant it lands on stdout
    # (see ``run_exec``). Opt-in — default ``None`` leaves every existing caller
    # (the whole daemon) byte-for-byte unchanged; only the Manager chat
    # front-door sets it, to stream the reply live.
    on_agent_message: Callable[[str], None] | None = None


class AgentCliRunner:
    def __init__(
        self,
        agent_bin: str | None = None,
        *,
        backend: RunnerBackend = DEFAULT_RUNNER_BACKEND,
        event_callback: EventCallback | None = None,
        default_extra_args: list[str] | None = None,
        before_exec: Callable[[], None] | None = None,
    ) -> None:
        self.backend = backend
        self.agent_bin = agent_bin or default_runner_bin(backend)
        self.event_callback = event_callback
        self.default_extra_args = list(default_extra_args or [])
        self.before_exec = before_exec

    def run_exec(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: RunnerOptions,
        run_label: str | None = None,
    ) -> AgentRunResult:
        if self.before_exec is not None:
            self.before_exec()
        # SOURCE-LEVEL gate: refuse to start a NEW LLM call if the (composed)
        # interrupt provider ALREADY signals a reason — a per-mission budget hit
        # its cap, or the operator/daemon requested a stop. Checked BEFORE the ACP
        # fast path and the CLI spawn, so the cap is enforced at the finest
        # granularity: once tripped no further call fires, and a single round can
        # never overspend past the cap while waiting for the between-rounds
        # breaker. A ``None`` provider (every non-mission call) makes this a no-op.
        _gate = options.external_interrupt_reason_provider
        if _gate is not None:
            try:
                _reason = _gate()
            except Exception:  # noqa: BLE001 — a provider fault must never wedge the call
                _reason = None
            if _reason:
                return AgentRunResult(
                    command=[self.agent_bin],
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    turn_completed=False,
                    turn_failed=True,
                    fatal_error=f"refused before start: {_reason}",
                )
        # Warm-copilot fast path: Manager front-door classify + direct replies go
        # through a persistent ``copilot --acp`` process.  The ACP client keeps
        # the classifier and conversation in separate logical sessions.
        if self._acp_enabled(run_label, options) and options.max_ai_credits is None:
            _acp = self._run_exec_acp(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=options,
                run_label=run_label,
            )
            if _acp is not None:
                return _acp
        options = self._apply_sandbox_policy(options)
        command = self._build_command(
            prompt=prompt, resume_thread_id=resume_thread_id, options=options
        )
        command[0] = self._resolve_executable(command[0])
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=options.working_dir or None,
            env=sandboxed_child_env() if options.sandbox_mode else None,
        )
        if self._prompt_via_stdin():
            self._write_prompt(process=process, prompt=prompt)
        else:
            self._close_stdin(process)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        events: list[dict] = []
        agent_messages: list[str] = []
        thread_id: str | None = resume_thread_id
        turn_completed = False
        turn_failed = False
        fatal_error: str | None = None

        line_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        soft_idle = options.watchdog_soft_idle_seconds or 0
        hard_idle = options.watchdog_hard_idle_seconds or 0
        last_activity_at = time.monotonic()
        last_soft_check_at = last_activity_at
        stdout_closed = False
        stderr_closed = False
        watchdog_terminated = False
        watchdog_reason: str | None = None

        def consume_pipe(stream_name: str, pipe) -> None:
            assert pipe is not None
            for line in pipe:
                line_queue.put((stream_name, line.rstrip("\n")))
            line_queue.put((stream_name, None))

        stdout_thread = threading.Thread(
            target=consume_pipe,
            args=("stdout", process.stdout),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=consume_pipe,
            args=("stderr", process.stderr),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        def check_external_interrupt() -> bool:
            nonlocal watchdog_reason, watchdog_terminated
            if watchdog_terminated or process.poll() is not None:
                return False
            if options.external_interrupt_reason_provider is None:
                return False
            interrupt_reason = options.external_interrupt_reason_provider()
            if not interrupt_reason:
                return False
            watchdog_reason = f"External interrupt: {interrupt_reason}"
            self._emit(
                self._stream_name("stderr", run_label),
                f"[watchdog] {watchdog_reason}",
            )
            self._terminate_process(process)
            watchdog_terminated = True
            return True

        while True:
            if process.poll() is not None and stdout_closed and stderr_closed:
                break
            check_external_interrupt()
            try:
                stream_name, text = line_queue.get(timeout=0.25)
            except KeyboardInterrupt:
                # Operator Ctrl-C while the agent CLI is "thinking": the main
                # thread blocks on this queue.get almost the entire subprocess
                # lifetime, so an interrupt lands here. Terminate the child
                # (terminate -> kill via the shared helper) so it is not
                # orphaned and does not keep burning tokens, then re-raise so
                # the REPL loop can return to its prompt.
                if process.poll() is None:
                    self._terminate_process(process)
                raise
            except queue.Empty:
                now = time.monotonic()
                idle_seconds = now - last_activity_at

                check_external_interrupt()

                if (
                    soft_idle > 0
                    and options.inactivity_callback is not None
                    and process.poll() is None
                    and idle_seconds >= soft_idle
                    and (now - last_soft_check_at) >= soft_idle
                ):
                    last_soft_check_at = now
                    snapshot = InactivitySnapshot(
                        idle_seconds=idle_seconds,
                        command=command,
                        thread_id=thread_id,
                        last_agent_message=agent_messages[-1] if agent_messages else "",
                        stdout_tail=stdout_lines[-50:],
                        stderr_tail=stderr_lines[-50:],
                        run_label=run_label,
                    )
                    decision = options.inactivity_callback(snapshot)
                    if decision == "restart":
                        watchdog_reason = (
                            f"Restart requested by stall sub-agent after {int(idle_seconds)}s idle."
                        )
                        self._emit(
                            self._stream_name("stderr", run_label),
                            f"[watchdog] {watchdog_reason}",
                        )
                        self._terminate_process(process)
                        watchdog_terminated = True

                if hard_idle > 0 and process.poll() is None and idle_seconds >= hard_idle:
                    watchdog_reason = (
                        f"Forced restart after hard idle timeout ({int(idle_seconds)}s)."
                    )
                    self._emit(
                        self._stream_name("stderr", run_label),
                        f"[watchdog] {watchdog_reason}",
                    )
                    self._terminate_process(process)
                    watchdog_terminated = True
                continue

            if text is None:
                if stream_name == "stdout":
                    stdout_closed = True
                else:
                    stderr_closed = True
                continue

            last_activity_at = time.monotonic()
            output_stream = self._stream_name(stream_name, run_label)
            self._emit(output_stream, text)

            if stream_name == "stdout":
                stdout_lines.append(text)
                event = self._parse_json_line(text)
                if event is None:
                    continue
                events.append(event)
                _msgs_before = len(agent_messages)
                (
                    thread_id,
                    turn_completed,
                    turn_failed,
                    fatal_error,
                ) = self._consume_event(
                    event=event,
                    thread_id=thread_id,
                    agent_messages=agent_messages,
                    turn_completed=turn_completed,
                    turn_failed=turn_failed,
                    fatal_error=fatal_error,
                )
                # Stream each NEW assistant block to the opt-in callback the
                # instant it lands — this is what lets the Manager chat front-door
                # render the reply live instead of after the whole turn. Default
                # ``None`` (every daemon/role turn) skips this entirely, so the
                # hot path is unchanged. A callback fault must never break the run.
                _cb = options.on_agent_message
                if _cb is not None and len(agent_messages) > _msgs_before:
                    for _blk in agent_messages[_msgs_before:]:
                        try:
                            _cb(_blk)
                        except Exception:  # noqa: BLE001 — UI callback must not break the turn
                            pass
            else:
                stderr_lines.append(text)

        if process.poll() is None:
            process.wait(timeout=10.0)

        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

        if watchdog_terminated:
            turn_failed = True
            if watchdog_reason and fatal_error is None:
                fatal_error = watchdog_reason
        elif turn_completed and not turn_failed:
            fatal_error = None
        elif process.returncode != 0 and fatal_error is None:
            turn_failed = True
            fatal_error = f"Process exited with code {process.returncode} before turn completion."
        elif not turn_completed and not agent_messages and fatal_error is None:
            # Some CLIs report configuration errors on stderr but still exit 0
            # (Copilot does this for an unavailable --model). A clean process
            # exit is not a successful model turn: preserve the concrete stderr
            # diagnostic so the supervisor fails fast instead of laundering it
            # into two rounds of "empty output" / no_progress.
            turn_failed = True
            fatal_error = _incomplete_turn_error(stderr_lines)

        return AgentRunResult(
            command=command,
            exit_code=process.returncode,
            thread_id=thread_id,
            agent_messages=agent_messages,
            json_events=events,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            turn_completed=turn_completed,
            turn_failed=turn_failed,
            fatal_error=fatal_error,
        )

    # ── warm-copilot (ACP) fast path ─────────────────────────────────────────
    def _acp_enabled(
        self,
        run_label: str | None,
        options: RunnerOptions | None = None,
    ) -> bool:
        """Route this call through the persistent ``copilot --acp`` client?

        True only for the copilot backend and a Manager label. It defaults ON;
        ``ARGUS_SKILL_COPILOT_ACP=0`` is the explicit rollback switch, while
        ``ARGUS_SKILL_COPILOT_ACP_LABELS`` overrides the default label set. All
        engineer/reviewer/planner/mission turns stay on the CLI ``Popen`` path.
        """
        if (
            self.backend != BACKEND_COPILOT
            or not run_label
            or getattr(options, "sandbox_mode", None) == "read-only"
        ):
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
        options: RunnerOptions,
        run_label: str | None,
    ) -> "AgentRunResult | None":
        """Run one prompt on the warm ACP client.

        A failure before an ACP session exists returns ``None`` so the caller can
        safely fall back to the ordinary CLI.  Once a conversational prompt may
        have started, return its failure instead of replaying a tool-capable turn
        in a second process (which could duplicate side effects).
        """
        try:
            from .copilot_acp import get_client

            client = get_client(
                self.agent_bin,
                options.model,
                options.reasoning_effort,
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
        except Exception:  # noqa: BLE001 — fast path must never break the turn
            return None
        if result.exit_code == 0 and result.turn_completed and result.agent_messages:
            return result
        if run_label in {"simple-1", "chat-1"} and result.thread_id:
            return result
        return None

    def _build_command(
        self, *, prompt: str, resume_thread_id: str | None, options: RunnerOptions
    ) -> list[str]:
        if self.backend == BACKEND_CLAUDE:
            return self._build_claude_command(resume_thread_id=resume_thread_id, options=options)
        if self.backend == BACKEND_COPILOT:
            return self._build_copilot_command(
                prompt=prompt, resume_thread_id=resume_thread_id, options=options
            )
        return self._build_codex_command(resume_thread_id=resume_thread_id, options=options)

    def _apply_sandbox_policy(self, options: RunnerOptions) -> RunnerOptions:
        """Gated, default-OFF containment chokepoint for codex builder roles.

        When ``ARGUS_SKILL_ENGINEER_SANDBOX`` is set, convert EVERY codex role
        into ``-s <mode>`` confined to its workdir plus the writable allowlist,
        clear the dangerous flags, and pin a ``-C`` (falling closed to a private
        scratch dir when the caller passed no workdir, so the writable workspace
        is NEVER the inherited cwd ``/``). This single chokepoint covers every
        AgentCliRunner role (engineer / reviewer / planner / manager classify /
        plan-mode), including ones that today fall through to codex's config
        default (danger-full-access on the box) because they set neither
        ``dangerous_yolo`` nor ``full_auto``. No-op when the gate is off, when an
        explicit ``sandbox_mode`` was already chosen, or for non-codex backends —
        so the default path stays byte-for-byte unchanged.
        """
        if self.backend in (BACKEND_CLAUDE, BACKEND_COPILOT):
            return options
        if options.sandbox_mode is not None:
            return options
        from ..core.sandbox import (
            engineer_sandbox_mode,
            fail_closed_workdir,
            writable_roots,
        )

        mode = engineer_sandbox_mode()
        if mode is None:
            # Gate OFF: byte-for-byte legacy behaviour for EVERY role.
            return options
        import dataclasses

        merged = list(dict.fromkeys([*(options.add_dirs or []), *writable_roots()]))
        # Fail closed: a sandboxed role with no -C would root its writable
        # workspace at the inherited cwd (the daemon's "/"). Pin a contained dir.
        working_dir = options.working_dir or fail_closed_workdir()
        return dataclasses.replace(
            options,
            sandbox_mode=mode,
            dangerous_yolo=False,
            full_auto=False,
            add_dirs=merged,
            working_dir=working_dir,
        )

    def _build_codex_command(
        self, *, resume_thread_id: str | None, options: RunnerOptions
    ) -> list[str]:
        command = [self.agent_bin, "exec"]
        if resume_thread_id:
            command.append("resume")
        command.append("--json")
        if options.model:
            command.extend(["-m", options.model])
        if options.reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{options.reasoning_effort}"'])
            # Stream a reasoning summary DURING the turn so the operator sees the
            # model is actively working instead of a silent "no stream output"
            # gap — gpt-5.x at high effort reasons server-side for tens of
            # seconds emitting nothing otherwise, which reads like a hang. "auto"
            # lets the model size the summary; ARGUS_SKILL_REASONING_SUMMARY=none
            # opts back out.
            summary = (os.environ.get("ARGUS_SKILL_REASONING_SUMMARY") or "auto").strip()
            if summary.lower() not in {"none", "off", "0", "false", ""}:
                command.extend(["-c", f'model_reasoning_summary="{summary}"'])
        if options.sandbox_mode and resume_thread_id:
            command.extend(["-c", f'sandbox_mode="{options.sandbox_mode}"'])
        elif options.sandbox_mode:
            # Sandboxed role: confine writes to the workspace (-C) plus the
            # explicit --add-dir allowlist; keep network on for research. This
            # replaces the dangerous bypass so the engineer cannot write the
            # package source / edit its own gate. The writable allowlist is the
            # caller's responsibility (it MUST exclude ~/.argus-skill, the
            # package, and ~/.codex).
            command.extend(["-s", options.sandbox_mode])
            # Always pin -C. Emitting -s workspace-write with no -C roots the
            # writable workspace at the inherited cwd (the daemon's "/"), which
            # would expose the whole FS — fall closed to a private scratch dir.
            if options.working_dir:
                command.extend(["-C", options.working_dir])
            else:
                from ..core.sandbox import fail_closed_workdir

                command.extend(["-C", fail_closed_workdir()])
            for extra_dir in options.add_dirs or []:
                command.extend(["--add-dir", extra_dir])
            if options.sandbox_mode == "workspace-write":
                # workspace-write defaults network OFF; force it on explicitly
                # rather than relying on the agent-writable config.toml.
                command.extend(["-c", "sandbox_workspace_write.network_access=true"])
        elif options.dangerous_yolo:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif options.full_auto:
            command.append("--full-auto")
        if options.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if getattr(options, "live_search", False):
            # codex exec enables live web search via CONFIG, not a flag (there is
            # no `--search` on `exec`). Valid ``web_search`` variants are
            # disabled/cached/indexed/live; force ``live`` so idea discovery does
            # real live searches instead of the cached default.
            command.extend(["-c", 'web_search="live"'])
        if options.output_schema_path and not resume_thread_id:
            command.extend(["--output-schema", options.output_schema_path])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_CODEX,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.append(resume_thread_id)
        # Always stream the prompt through stdin so multiline prompts survive
        # Windows `.cmd` wrappers and do not appear in process lists.
        command.append("-")
        return command

    def _build_claude_command(
        self, *, resume_thread_id: str | None, options: RunnerOptions
    ) -> list[str]:
        command = [
            self.agent_bin,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
        ]
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            effort = (
                "high"
                if options.reasoning_effort == "xhigh"
                else options.reasoning_effort
            )
            command.extend(["--effort", effort])
        if options.max_budget_usd is not None and options.max_budget_usd > 0:
            command.extend(["--max-budget-usd", format(options.max_budget_usd, ".12g")])
        if options.sandbox_mode == "read-only":
            command.extend(["--tools", "Read,Glob,Grep"])
        elif options.dangerous_yolo:
            command.extend(["--permission-mode", "bypassPermissions"])
        elif options.full_auto:
            command.extend(["--permission-mode", "acceptEdits"])
        if options.output_schema_path and not resume_thread_id:
            command.extend(
                ["--json-schema", self._load_compact_schema_text(options.output_schema_path)]
            )

        # --add-dir
        if options.add_dirs:
            for dir_path in options.add_dirs:
                command.extend(["--add-dir", dir_path])

        # --plugin-dir
        if options.plugin_dirs:
            for dir_path in options.plugin_dirs:
                command.extend(["--plugin-dir", dir_path])

        # --file
        if options.file_specs:
            for file_spec in options.file_specs:
                command.extend(["--file", file_spec])

        # --worktree
        if options.worktree_name:
            command.extend(["--worktree", options.worktree_name])

        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_CLAUDE,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        return command

    def _build_copilot_command(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: RunnerOptions,
    ) -> list[str]:
        command = [
            self.agent_bin,
            "--output-format",
            "json",
            "--stream",
            "on",
            "--no-auto-update",
            "--no-ask-user",
        ]
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            command.extend(["--reasoning-effort", options.reasoning_effort])
        if options.max_ai_credits is not None and options.max_ai_credits >= 30:
            command.extend(["--max-ai-credits", str(options.max_ai_credits)])
        if options.sandbox_mode == "read-only":
            command.extend([
                "--available-tools", "view,rg,glob",
                "--allow-tool", "view,rg,glob",
            ])
        elif options.dangerous_yolo:
            command.append("--yolo")
        else:
            # Copilot prompt mode requires automatic tool approval in non-interactive runs.
            command.append("--allow-all-tools")
        if options.add_dirs:
            for dir_path in options.add_dirs:
                command.extend(["--add-dir", dir_path])
        if options.plugin_dirs:
            for dir_path in options.plugin_dirs:
                command.extend(["--plugin-dir", dir_path])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if options.sandbox_mode == "read-only":
            merged_extra_args = _read_only_extra_args(
                merged_extra_args, backend=BACKEND_COPILOT,
            )
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        # Copilot CLI (@github/copilot) has NO structured-output / schema flag
        # (codex --output-schema, claude --json-schema); its --output-format json
        # only wraps EVENTS while the assistant CONTENT stays free-form. The
        # reviewer/planner need a schema-valid JSON verdict (reviewer is the SOLE
        # done-authority), so on copilot we embed the compact schema + a strict
        # "JSON only" instruction IN THE PROMPT so the model self-constrains.
        # Skip on a resumed thread (the contract already lives in the
        # conversation) and fail-open. / copilot CLI 无结构化输出/schema 参数，
        # --output-format json 只包事件、内容仍自由文本；reviewer/planner 需要
        # schema 合法的 JSON 裁决，故在 prompt 里嵌入压缩 schema + 严格"只回 JSON"
        # 指令让模型自约束；resume 时跳过（契约已在对话里），失败不阻塞。
        effective_prompt = prompt
        if options.output_schema_path and not resume_thread_id:
            suffix = self._copilot_schema_suffix(options.output_schema_path)
            if suffix:
                effective_prompt = prompt + suffix
        command.extend(["-p", effective_prompt])
        return command

    @staticmethod
    def _write_prompt(*, process: subprocess.Popen[str], prompt: str) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(prompt)
            if not prompt.endswith("\n"):
                process.stdin.write("\n")
        except BrokenPipeError:
            return
        finally:
            try:
                process.stdin.close()
            except OSError:
                return

    @staticmethod
    def _close_stdin(process: subprocess.Popen[str]) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.close()
        except OSError:
            return

    def _prompt_via_stdin(self) -> bool:
        return self.backend != BACKEND_COPILOT

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        if os.path.dirname(executable) or "/" in executable or "\\" in executable:
            return executable
        resolved = shutil.which(executable)
        if resolved:
            return resolved
        return executable

    @staticmethod
    def _parse_json_line(line: str) -> dict | None:
        stripped = line.strip()
        if not stripped.startswith("{"):
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _load_compact_schema_text(path: str) -> str:
        raw = Path(path).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))

    def _copilot_schema_suffix(self, schema_path: str) -> str:
        """Prompt-embedded output contract for backends without a schema flag.

        EN: Copilot has no ``--output-schema``. Append the compact JSON Schema +
        a strict "reply with ONLY schema-valid JSON" instruction so the
        reviewer/planner verdict parses instead of degrading to a prose reply
        (which the strict parser rejects → the reviewer, the sole done-authority,
        would fall back to ``continue``). Fail-soft to "" — a missing/invalid
        schema must never block a run.
        中文：copilot 没有 ``--output-schema``。把压缩后的 JSON Schema + 严格
        "只回合法 JSON"指令追加到 prompt，让 reviewer/planner 裁决可解析，而不是
        退化成散文（严格 parser 会拒 → reviewer 退回 ``continue``）。schema
        缺失/非法时返回 ""，绝不阻塞运行。
        """
        try:
            schema_text = self._load_compact_schema_text(schema_path)
        except Exception:  # noqa: BLE001 — no/invalid schema → no suffix, fail-open
            return ""
        if not schema_text.strip():
            return ""
        return (
            "\n\n--- OUTPUT CONTRACT (STRICT) ---\n"
            "Your FINAL message MUST be exactly one JSON object that validates "
            "against this JSON Schema. No prose, no markdown fences, nothing "
            "before or after it:\n"
            f"{schema_text}\n"
        )

    def _emit(self, stream: str, line: str) -> None:
        if self.event_callback is None:
            return
        self.event_callback(stream, line)

    def _consume_event(
        self,
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        if self.backend == BACKEND_CLAUDE:
            return self._consume_claude_event(
                event=event,
                thread_id=thread_id,
                agent_messages=agent_messages,
                turn_completed=turn_completed,
                turn_failed=turn_failed,
                fatal_error=fatal_error,
            )
        if self.backend == BACKEND_COPILOT:
            return self._consume_copilot_event(
                event=event,
                thread_id=thread_id,
                agent_messages=agent_messages,
                turn_completed=turn_completed,
                turn_failed=turn_failed,
                fatal_error=fatal_error,
            )
        return self._consume_codex_event(
            event=event,
            thread_id=thread_id,
            agent_messages=agent_messages,
            turn_completed=turn_completed,
            turn_failed=turn_failed,
            fatal_error=fatal_error,
        )

    @staticmethod
    def _consume_codex_event(
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id", thread_id)
        elif event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                message = item.get("text", "")
                if isinstance(message, str):
                    agent_messages.append(message)
        elif event_type == "turn.completed":
            turn_completed = True
        elif event_type == "turn.failed":
            turn_failed = True
            err = event.get("error", {})
            if isinstance(err, dict):
                maybe_msg = err.get("message")
                if isinstance(maybe_msg, str):
                    fatal_error = maybe_msg
        elif event_type == "error" and fatal_error is None:
            maybe_msg = event.get("message")
            if isinstance(maybe_msg, str):
                fatal_error = maybe_msg
        return thread_id, turn_completed, turn_failed, fatal_error

    @staticmethod
    def _consume_claude_event(
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        event_type = str(event.get("type") or "").strip()
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            thread_id = session_id

        if event_type == "assistant":
            message = event.get("message")
            text = AgentCliRunner._extract_claude_message_text(message)
            if text:
                agent_messages.append(text)
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type != "result":
            return thread_id, turn_completed, turn_failed, fatal_error

        structured_output = event.get("structured_output")
        if structured_output is not None:
            text = json.dumps(structured_output, ensure_ascii=True)
            if not agent_messages or agent_messages[-1] != text:
                agent_messages.append(text)
        else:
            result_text = event.get("result")
            if isinstance(result_text, str):
                normalized = result_text.strip()
                if normalized and (not agent_messages or agent_messages[-1].strip() != normalized):
                    agent_messages.append(normalized)

        is_error = bool(event.get("is_error", False))
        subtype = str(event.get("subtype") or "").strip()
        if not is_error and subtype == "success":
            turn_completed = True
            return thread_id, turn_completed, turn_failed, fatal_error

        turn_failed = True
        if fatal_error is None:
            result_text = event.get("result")
            if isinstance(result_text, str) and result_text.strip():
                fatal_error = result_text.strip()
            else:
                fatal_error = f"Claude runner reported {subtype or 'error'}."
        return thread_id, turn_completed, turn_failed, fatal_error

    @staticmethod
    def _consume_copilot_event(
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        event_type = str(event.get("type") or "").strip()
        data = event.get("data")
        if event_type == "assistant.message" and isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                agent_messages.append(content.strip())
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "error":
            turn_failed = True
            if fatal_error is None:
                if isinstance(data, dict):
                    maybe_msg = data.get("message")
                    if isinstance(maybe_msg, str) and maybe_msg.strip():
                        fatal_error = maybe_msg.strip()
                if fatal_error is None:
                    maybe_msg = event.get("message")
                    if isinstance(maybe_msg, str) and maybe_msg.strip():
                        fatal_error = maybe_msg.strip()
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type != "result":
            return thread_id, turn_completed, turn_failed, fatal_error

        session_id = event.get("sessionId")
        if isinstance(session_id, str) and session_id.strip():
            thread_id = session_id

        exit_code = event.get("exitCode")
        if exit_code == 0:
            turn_completed = True
            return thread_id, turn_completed, turn_failed, fatal_error

        turn_failed = True
        if fatal_error is None:
            fatal_error = f"Copilot CLI exited with code {exit_code}."
        return thread_id, turn_completed, turn_failed, fatal_error

    @staticmethod
    def _extract_claude_message_text(message: object) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            # A child stuck in uninterruptible sleep (D-state) / under ptrace may
            # not be reaped immediately even after SIGKILL, so this wait can time
            # out again. Mirror CPython's subprocess.run: swallow it and give up
            # gracefully rather than letting it abort the caller.
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _stream_name(stream: str, run_label: str | None) -> str:
        if not run_label:
            return stream
        return f"{run_label}.{stream}"
