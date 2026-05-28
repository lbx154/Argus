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

from .models import CodexRunResult
from .runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_COPILOT,
    BACKEND_CODEX,
    DEFAULT_RUNNER_BACKEND,
    RunnerBackend,
    default_runner_bin,
)


EventCallback = Callable[[str, str], None]
InactivityDecision = Literal["continue", "restart"]


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
    skip_git_repo_check: bool = False
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


class CodexRunner:
    def __init__(
        self,
        codex_bin: str | None = None,
        *,
        backend: RunnerBackend = DEFAULT_RUNNER_BACKEND,
        event_callback: EventCallback | None = None,
        default_extra_args: list[str] | None = None,
        before_exec: Callable[[], None] | None = None,
    ) -> None:
        self.backend = backend
        self.codex_bin = codex_bin or default_runner_bin(backend)
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
    ) -> CodexRunResult:
        if self.before_exec is not None:
            self.before_exec()
        command = self._build_command(prompt=prompt, resume_thread_id=resume_thread_id, options=options)
        command[0] = self._resolve_executable(command[0])
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=options.working_dir or None,
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

                if (
                    hard_idle > 0
                    and process.poll() is None
                    and idle_seconds >= hard_idle
                ):
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

        return CodexRunResult(
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

    def _build_command(self, *, prompt: str, resume_thread_id: str | None, options: RunnerOptions) -> list[str]:
        if self.backend == BACKEND_CLAUDE:
            return self._build_claude_command(resume_thread_id=resume_thread_id, options=options)
        if self.backend == BACKEND_COPILOT:
            return self._build_copilot_command(prompt=prompt, resume_thread_id=resume_thread_id, options=options)
        return self._build_codex_command(resume_thread_id=resume_thread_id, options=options)

    def _build_codex_command(self, *, resume_thread_id: str | None, options: RunnerOptions) -> list[str]:
        command = [self.codex_bin, "exec"]
        if resume_thread_id:
            command.append("resume")
        command.append("--json")
        if options.model:
            command.extend(["-m", options.model])
        if options.reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{options.reasoning_effort}"'])
        if options.dangerous_yolo:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif options.full_auto:
            command.append("--full-auto")
        if options.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if options.output_schema_path and not resume_thread_id:
            command.extend(["--output-schema", options.output_schema_path])
        merged_extra_args = [*self.default_extra_args]
        if options.extra_args:
            merged_extra_args.extend(options.extra_args)
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.append(resume_thread_id)
        # Always stream the prompt through stdin so multiline prompts survive
        # Windows `.cmd` wrappers and do not appear in process lists.
        command.append("-")
        return command

    def _build_claude_command(self, *, resume_thread_id: str | None, options: RunnerOptions) -> list[str]:
        command = [
            self.codex_bin,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
        ]
        if options.model:
            command.extend(["--model", options.model])
        if options.reasoning_effort:
            effort = "high" if options.reasoning_effort == "xhigh" else options.reasoning_effort
            command.extend(["--effort", effort])
        if options.dangerous_yolo:
            command.extend(["--permission-mode", "bypassPermissions"])
        elif options.full_auto:
            command.extend(["--permission-mode", "acceptEdits"])
        if options.output_schema_path and not resume_thread_id:
            command.extend(["--json-schema", self._load_compact_schema_text(options.output_schema_path)])

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
            self.codex_bin,
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
        if options.dangerous_yolo:
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
        if merged_extra_args:
            command.extend(merged_extra_args)
        if resume_thread_id:
            command.extend(["--resume", resume_thread_id])
        command.extend(["-p", prompt])
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
            text = CodexRunner._extract_claude_message_text(message)
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
            process.wait(timeout=5.0)

    @staticmethod
    def _stream_name(stream: str, run_label: str | None) -> str:
        if not run_label:
            return stream
        return f"{run_label}.{stream}"
