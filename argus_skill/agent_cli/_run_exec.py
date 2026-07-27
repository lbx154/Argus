"""``run_exec`` split into four explicit phases: the source-level interrupt gate
+ ACP fast path, spawning the child process, streaming its stdout/stderr with
watchdog enforcement, and finalizing the result (OpenCode recovery +
completion/failure semantics). Extracted verbatim from ``agent_cli_runner.py``
— same argv, stdin, env, event-callback ordering, bounded capture, and
process-group termination behaviour, just split across named helpers instead
of one 390-line method.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from ._env import (
    _CAPTURE_JSON_EVENTS_ENV,
    _CAPTURE_STDERR_LINES_ENV,
    _CAPTURE_STDOUT_LINES_ENV,
    _DEFAULT_CAPTURE_JSON_EVENTS,
    _DEFAULT_CAPTURE_STDERR_LINES,
    _DEFAULT_CAPTURE_STDOUT_LINES,
    _DEFAULT_STREAM_QUEUE_LINES,
    _STREAM_QUEUE_LINES_ENV,
    _incomplete_turn_error,
    _positive_env_int,
    _turn_wall_clock_seconds,
)
from ._idle_watchdog import (
    STALLED_STAGE,
    TERMINATE_STAGE,
    WARNING_STAGE,
    IdleEscalation,
)
from .models import AgentRunResult, InactivitySnapshot
from .runner_backend import BACKEND_OPENCODE

_POST_EXIT_PIPE_DRAIN_QUIET_SECONDS = 0.1
_POST_EXIT_PIPE_DRAIN_MAX_SECONDS = 5.0
_ORPHAN_GROUP_DETACH_GRACE_SECONDS = 0.5


@dataclass
class _StreamState:
    """Mutable accumulator threaded through the stream/finalize phases.

    Field-for-field the same values that used to be a fistful of local
    variables (and ``nonlocal`` closures) inside the single ``run_exec``
    method — grouping them lets the streaming loop and the finalize step live
    in separate methods without changing what is read or written or when.
    """

    thread_id: str | None
    stdout_lines: "deque[str]" = field(default_factory=deque)
    stderr_lines: "deque[str]" = field(default_factory=deque)
    events: "deque[dict]" = field(default_factory=deque)
    stdout_line_count: int = 0
    stderr_line_count: int = 0
    json_event_count: int = 0
    agent_messages: list[str] = field(default_factory=list)
    turn_completed: bool = False
    turn_failed: bool = False
    fatal_error: str | None = None
    tool_activity_observed: bool = False
    usage_model: str = ""
    watchdog_terminated: bool = False
    watchdog_reason: str | None = None
    orphan_process_group_id: int = 0
    orphan_process_group_cleanup_succeeded: bool = False
    process_group_cleanup_checked: bool = False


class RunExecMixin:
    """Owns the public ``run_exec`` entry point and its private phases."""

    def run_exec(
        self,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options,
        run_label: str | None = None,
    ) -> AgentRunResult:
        if self.before_exec is not None:
            self.before_exec()
        gated = self._run_exec_start_gate(resume_thread_id=resume_thread_id, options=options)
        if gated is not None:
            return gated
        # Warm-copilot fast path: Manager front-door classify + direct replies go
        # through a persistent ``copilot --acp`` process.  The ACP client keeps
        # the classifier and conversation in separate logical sessions.
        if self._acp_enabled(run_label, options):
            acp_result = self._run_exec_acp(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=options,
                run_label=run_label,
            )
            if acp_result is not None:
                return acp_result
        options = self._apply_sandbox_policy(options)
        command, process, spawn_failure = self._spawn_turn_process(
            prompt=prompt, resume_thread_id=resume_thread_id, options=options
        )
        if spawn_failure is not None:
            return spawn_failure
        state = self._stream_turn_output(
            process=process,
            command=command,
            options=options,
            run_label=run_label,
            thread_id=resume_thread_id,
        )
        return self._finalize_turn_result(
            process=process, command=command, options=options, state=state
        )

    @classmethod
    def _cleanup_orphan_process_group(
        cls,
        process: subprocess.Popen[str],
        state: _StreamState,
    ) -> None:
        """Clean descendants left in this turn's private process group.

        Durable Argus jobs launch in their own session/process group. Anything
        still in the provider turn's group after the provider itself exits has
        no durable owner, regardless of the command text that created it.
        """
        process_group_id = int(getattr(process, "pid", 0) or 0)
        if (
            os.name == "nt"
            or process_group_id <= 0
            or not cls._process_group_alive(process_group_id)
        ):
            return
        # A durable child launched through `setsid ... &` is independently
        # owned, but under scheduler pressure the provider shell can exit just
        # before the child executes setsid(). Give that child a brief chance to
        # leave the provider group; genuine leaked descendants remain and are
        # terminated below.
        if cls._wait_process_group_exit(
            process_group_id,
            _ORPHAN_GROUP_DETACH_GRACE_SECONDS,
        ):
            return
        state.orphan_process_group_id = process_group_id
        cls._terminate_process(process)
        state.orphan_process_group_cleanup_succeeded = not cls._process_group_alive(
            process_group_id
        )

    def _run_exec_start_gate(
        self, *, resume_thread_id: str | None, options
    ) -> AgentRunResult | None:
        # SOURCE-LEVEL gate: refuse to start a NEW LLM call if the (composed)
        # interrupt provider ALREADY signals a reason — a per-mission budget hit
        # its cap, or the operator/daemon requested a stop. Checked BEFORE the ACP
        # fast path and the CLI spawn, so the cap is enforced at the finest
        # granularity: once tripped no further call fires, and a single round can
        # never overspend past the cap while waiting for the between-rounds
        # breaker. A ``None`` provider (every non-mission call) makes this a no-op.
        _gate = options.external_interrupt_reason_provider
        if _gate is None:
            return None
        try:
            _reason = _gate()
        except Exception:  # noqa: BLE001 — a provider fault must never wedge the call
            _reason = None
        if not _reason:
            return None
        return AgentRunResult(
            command=[self.agent_bin],
            exit_code=-1,
            thread_id=resume_thread_id,
            turn_completed=False,
            turn_failed=True,
            fatal_error=f"refused before start: {_reason}",
        )

    def _spawn_turn_process(
        self, *, prompt: str, resume_thread_id: str | None, options
    ) -> tuple[list[str], subprocess.Popen[str] | None, AgentRunResult | None]:
        command = self._build_command(
            resume_thread_id=resume_thread_id, options=options
        )
        effective_prompt = self._effective_prompt(
            prompt=prompt,
            resume_thread_id=resume_thread_id,
            options=options,
        )
        command, stdin_prompt = self._prepare_prompt_delivery(
            command,
            effective_prompt,
        )
        command[0] = self._resolve_executable(command[0])
        if options.isolate_workdir:
            try:
                from ..core.sandbox import isolated_workdir_command

                command = isolated_workdir_command(
                    command,
                    working_dir=options.working_dir,
                )
            except RuntimeError as exc:
                return (
                    command,
                    None,
                    AgentRunResult(
                        command=[self.agent_bin],
                        exit_code=-1,
                        thread_id=resume_thread_id,
                        turn_completed=False,
                        turn_failed=True,
                        fatal_error=f"maintenance isolation unavailable: {exc}",
                    ),
                )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Pin UTF-8 explicitly: without this, text mode uses the OS locale
            # encoding, which is cp1252 on Windows and raises UnicodeEncodeError
            # when the prompt or streamed model output contains non-Latin-1
            # characters (e.g. "\u2192", CJK, emoji). errors="replace" keeps the
            # reader from crashing on malformed bytes mid-stream.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=options.working_dir or None,
            env=self._child_env(options),
            start_new_session=os.name != "nt",
        )
        if stdin_prompt is not None:
            self._write_prompt(
                process=process,
                prompt=stdin_prompt,
            )
        else:
            self._close_stdin(process)
        return command, process, None

    def _stream_turn_output(
        self,
        *,
        process: subprocess.Popen[str],
        command: list[str],
        options,
        run_label: str | None,
        thread_id: str | None,
    ) -> _StreamState:
        state = _StreamState(
            thread_id=thread_id,
            stdout_lines=deque(
                maxlen=_positive_env_int(
                    _CAPTURE_STDOUT_LINES_ENV,
                    _DEFAULT_CAPTURE_STDOUT_LINES,
                )
            ),
            stderr_lines=deque(
                maxlen=_positive_env_int(
                    _CAPTURE_STDERR_LINES_ENV,
                    _DEFAULT_CAPTURE_STDERR_LINES,
                )
            ),
            events=deque(
                maxlen=_positive_env_int(
                    _CAPTURE_JSON_EVENTS_ENV,
                    _DEFAULT_CAPTURE_JSON_EVENTS,
                )
            ),
        )

        line_queue: queue.Queue[tuple[str, str | None]] = queue.Queue(
            maxsize=_positive_env_int(
                _STREAM_QUEUE_LINES_ENV,
                _DEFAULT_STREAM_QUEUE_LINES,
            )
        )
        stop_queueing = threading.Event()
        last_reader_enqueue_at = [time.monotonic()]
        soft_idle = options.watchdog_soft_idle_seconds or 0
        stalled_idle = options.watchdog_stalled_idle_seconds or 0
        hard_idle = options.watchdog_hard_idle_seconds or 0
        idle_escalation = IdleEscalation(
            warning_seconds=soft_idle,
            stalled_seconds=stalled_idle,
            terminate_seconds=hard_idle,
        )
        last_activity_at = time.monotonic()
        turn_started_at = last_activity_at
        turn_wall_clock_seconds = _turn_wall_clock_seconds(run_label)
        last_soft_check_at = last_activity_at
        provider_exited_at: float | None = None
        stdout_closed = False
        stderr_closed = False

        def consume_pipe(stream_name: str, pipe) -> None:
            assert pipe is not None
            for line in pipe:
                if stop_queueing.is_set():
                    # Keep draining the OS pipe so an independently owned
                    # process cannot block on inherited stdout/stderr, but no
                    # longer retain output after the provider drain closes.
                    continue
                item = (stream_name, line.rstrip("\n"))
                while not stop_queueing.is_set():
                    try:
                        line_queue.put(item, timeout=0.1)
                        last_reader_enqueue_at[0] = time.monotonic()
                        break
                    except queue.Full:
                        continue
            if stop_queueing.is_set():
                return
            while not stop_queueing.is_set():
                try:
                    line_queue.put((stream_name, None), timeout=0.1)
                    last_reader_enqueue_at[0] = time.monotonic()
                    return
                except queue.Full:
                    continue

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
            if state.watchdog_terminated or process.poll() is not None:
                return False
            if options.external_interrupt_reason_provider is None:
                return False
            interrupt_reason = options.external_interrupt_reason_provider()
            if not interrupt_reason:
                return False
            state.watchdog_reason = f"External interrupt: {interrupt_reason}"
            self._emit(
                self._stream_name("stderr", run_label),
                f"[watchdog] {state.watchdog_reason}",
            )
            self._terminate_process(process)
            state.watchdog_terminated = True
            return True

        def check_wall_clock_limit() -> bool:
            if (
                state.watchdog_terminated
                or process.poll() is not None
                or turn_wall_clock_seconds <= 0
                or time.monotonic() - turn_started_at < turn_wall_clock_seconds
            ):
                return False
            subject = (
                "scientist skill distill"
                if str(run_label or "").strip().lower() == "scientist.skill_distill"
                else "engineer turn"
            )
            state.watchdog_reason = (
                f"External interrupt: {subject} time budget reached after "
                f"{turn_wall_clock_seconds}s; yield for review/steering"
            )
            self._emit(
                self._stream_name("stderr", run_label),
                f"[watchdog] {state.watchdog_reason}",
            )
            self._terminate_process(process)
            state.watchdog_terminated = True
            return True

        while True:
            if process.poll() is not None:
                if provider_exited_at is None:
                    provider_exited_at = time.monotonic()
                if not state.process_group_cleanup_checked:
                    state.process_group_cleanup_checked = True
                    self._cleanup_orphan_process_group(process, state)
                if stdout_closed and stderr_closed:
                    break
                post_exit_elapsed = time.monotonic() - provider_exited_at
                reader_quiet = (
                    time.monotonic() - last_reader_enqueue_at[0]
                    >= _POST_EXIT_PIPE_DRAIN_QUIET_SECONDS
                )
                if post_exit_elapsed >= _POST_EXIT_PIPE_DRAIN_MAX_SECONDS or (
                    post_exit_elapsed >= _POST_EXIT_PIPE_DRAIN_QUIET_SECONDS
                    and reader_quiet
                    and line_queue.empty()
                ):
                    # A separately owned durable process may inherit the
                    # provider's pipes. Stop retaining new output after a
                    # bounded/quiet drain, then consume everything already
                    # queued before returning. Reader threads continue
                    # discarding from the OS pipe until its real owner closes.
                    stop_queueing.set()
                    if line_queue.empty():
                        break
            check_external_interrupt()
            check_wall_clock_limit()
            try:
                stream_name, text = line_queue.get(timeout=0.25)
            except KeyboardInterrupt:
                # Operator Ctrl-C while the agent CLI is "thinking": the main
                # thread blocks on this queue.get almost the entire subprocess
                # lifetime, so an interrupt lands here. Terminate the child
                # (terminate -> kill via the shared helper) so it is not
                # orphaned and does not keep burning tokens, then re-raise so
                # the interactive caller can return to its prompt.
                if process.poll() is None:
                    self._terminate_process(process)
                raise
            except queue.Empty:
                now = time.monotonic()
                idle_seconds = now - last_activity_at

                check_external_interrupt()
                check_wall_clock_limit()

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
                        thread_id=state.thread_id,
                        last_agent_message=(
                            state.agent_messages[-1] if state.agent_messages else ""
                        ),
                        stdout_tail=list(state.stdout_lines)[-50:],
                        stderr_tail=list(state.stderr_lines)[-50:],
                        run_label=run_label,
                    )
                    decision = options.inactivity_callback(snapshot)
                    if decision == "restart":
                        state.watchdog_reason = (
                            f"Restart requested by stall sub-agent after {int(idle_seconds)}s idle."
                        )
                        self._emit(
                            self._stream_name("stderr", run_label),
                            f"[watchdog] {state.watchdog_reason}",
                        )
                        self._terminate_process(process)
                        state.watchdog_terminated = True

                last_message_chars = len(state.agent_messages[-1]) if state.agent_messages else 0
                for stage in idle_escalation.newly_due(idle_seconds):
                    if process.poll() is not None:
                        break
                    if stage == WARNING_STAGE:
                        self._emit(
                            self._stream_name("stderr", run_label),
                            "[watchdog] No model stream event for "
                            f"{int(idle_seconds)}s (warning threshold "
                            f"{soft_idle}s, pid={process.pid}, "
                            f"thread={state.thread_id or '-'}, "
                            f"stdout_lines={state.stdout_line_count}, "
                            f"stderr_lines={state.stderr_line_count}, "
                            f"last_message_chars={last_message_chars}); "
                            "capturing diagnostics and continuing.",
                        )
                    elif stage == STALLED_STAGE:
                        self._emit(
                            self._stream_name("stderr", run_label),
                            "[watchdog] Model call is likely stalled after "
                            f"{int(idle_seconds)}s without a stream event "
                            f"(threshold {stalled_idle}s, pid={process.pid}); "
                            f"stdout_lines={state.stdout_line_count}, "
                            f"stderr_lines={state.stderr_line_count}; continuing "
                            "until the hard deadline.",
                        )
                    elif stage == TERMINATE_STAGE:
                        state.watchdog_reason = (
                            "Forced restart after hard idle timeout "
                            f"({hard_idle}s without a model stream event)."
                        )
                        self._emit(
                            self._stream_name("stderr", run_label),
                            f"[watchdog] {state.watchdog_reason}",
                        )
                        self._terminate_process(process)
                        state.watchdog_terminated = True
                continue

            if text is None:
                if stream_name == "stdout":
                    stdout_closed = True
                else:
                    stderr_closed = True
                continue

            last_activity_at = time.monotonic()
            idle_escalation.reset()
            output_stream = self._stream_name(stream_name, run_label)
            self._emit(output_stream, text)

            if stream_name == "stdout":
                state.stdout_line_count += 1
                state.stdout_lines.append(text)
                event = self._parse_json_line(text)
                if event is None:
                    continue
                state.json_event_count += 1
                if self._event_has_tool_activity(event):
                    state.tool_activity_observed = True
                observed_model = self._event_usage_model(event)
                if observed_model:
                    state.usage_model = observed_model
                if self._retain_json_event(event):
                    state.events.append(event)
                _msgs_before = len(state.agent_messages)
                (
                    state.thread_id,
                    state.turn_completed,
                    state.turn_failed,
                    state.fatal_error,
                ) = self._consume_event(
                    event=event,
                    thread_id=state.thread_id,
                    agent_messages=state.agent_messages,
                    turn_completed=state.turn_completed,
                    turn_failed=state.turn_failed,
                    fatal_error=state.fatal_error,
                )
                # Stream each NEW assistant block to the opt-in callback the
                # instant it lands — this is what lets the Manager chat front-door
                # render the reply live instead of after the whole turn. Default
                # ``None`` (every daemon/role turn) skips this entirely, so the
                # hot path is unchanged. A callback fault must never break the run.
                _cb = options.on_agent_message
                if _cb is not None and len(state.agent_messages) > _msgs_before:
                    for _blk in state.agent_messages[_msgs_before:]:
                        try:
                            _cb(_blk)
                        except Exception:  # noqa: BLE001 — UI callback must not break the turn
                            pass
            else:
                state.stderr_line_count += 1
                state.stderr_lines.append(text)

        stop_queueing.set()
        if process.poll() is None:
            process.wait(timeout=10.0)

        stdout_thread.join(timeout=2.0 if stdout_closed else 0.05)
        stderr_thread.join(timeout=2.0 if stderr_closed else 0.05)
        return state

    def _finalize_turn_result(
        self,
        *,
        process: subprocess.Popen[str],
        command: list[str],
        options,
        state: _StreamState,
    ) -> AgentRunResult:
        if (
            self.backend == BACKEND_OPENCODE
            and process.returncode == 0
            and not state.watchdog_terminated
            and not state.turn_completed
            and not state.turn_failed
            and state.fatal_error is None
            and state.thread_id
        ):
            recovered_events, recovery_error = self._recover_opencode_events(
                thread_id=state.thread_id,
                observed_events=list(state.events),
                options=options,
            )
            if recovery_error is not None:
                state.turn_failed = True
                state.fatal_error = recovery_error
            else:
                for event in recovered_events:
                    state.json_event_count += 1
                    if self._event_has_tool_activity(event):
                        state.tool_activity_observed = True
                    observed_model = self._event_usage_model(event)
                    if observed_model:
                        state.usage_model = observed_model
                    if self._retain_json_event(event):
                        state.events.append(event)
                    messages_before = len(state.agent_messages)
                    (
                        state.thread_id,
                        state.turn_completed,
                        state.turn_failed,
                        state.fatal_error,
                    ) = self._consume_opencode_event(
                        event=event,
                        thread_id=state.thread_id,
                        agent_messages=state.agent_messages,
                        turn_completed=state.turn_completed,
                        turn_failed=state.turn_failed,
                        fatal_error=state.fatal_error,
                    )
                    callback = options.on_agent_message
                    if callback is not None and len(state.agent_messages) > messages_before:
                        for message in state.agent_messages[messages_before:]:
                            try:
                                callback(message)
                            except Exception:  # noqa: BLE001 — UI callback must not break the turn
                                pass

        if state.watchdog_terminated:
            state.turn_failed = True
            if state.watchdog_reason and state.fatal_error is None:
                state.fatal_error = state.watchdog_reason
        elif state.turn_completed and not state.turn_failed:
            state.fatal_error = None
        elif process.returncode != 0 and state.fatal_error is None:
            state.turn_failed = True
            state.fatal_error = (
                f"Process exited with code {process.returncode} before turn completion."
            )
        elif not state.turn_completed and state.fatal_error is None:
            # A provider message is not a terminal turn receipt. Copilot can
            # exit 0 after emitting assistant/tool deltas without the final
            # ``result`` event; accepting that partial stream loses sessionId,
            # records thread_id=null, and lets an unfinished Engineer round
            # advance to review. Fail closed unless the backend emitted its
            # authoritative completion event. Preserve stderr when available
            # so configuration failures still retain their concrete diagnosis.
            state.turn_failed = True
            state.fatal_error = _incomplete_turn_error(state.stderr_lines)

        return AgentRunResult(
            command=command,
            exit_code=process.returncode,
            thread_id=state.thread_id,
            agent_messages=state.agent_messages,
            json_events=list(state.events),
            stdout_lines=list(state.stdout_lines),
            stderr_lines=list(state.stderr_lines),
            stdout_line_count=state.stdout_line_count,
            stderr_line_count=state.stderr_line_count,
            json_event_count=state.json_event_count,
            turn_completed=state.turn_completed,
            turn_failed=state.turn_failed,
            fatal_error=state.fatal_error,
            tool_activity_observed=state.tool_activity_observed,
            usage_model=state.usage_model,
            orphan_process_group_id=state.orphan_process_group_id,
            orphan_process_group_cleanup_succeeded=(state.orphan_process_group_cleanup_succeeded),
        )
