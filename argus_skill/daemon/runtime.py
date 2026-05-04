"""Daemon runtime: queue-based 7×24 wrapper around SkillLoop.

This is the new code argus-skill needs to support 24/7 operation. It:

  * Owns one SkillLoop instance (kept across tasks; the underlying
    SkillStore is reused so cache hits accumulate).
  * Receives ControlCommands from one or more channels (Telegram +
    JSONL bus). Commands enter a thread-safe queue.
  * Has one worker thread that pops the next ``run`` command, calls
    ``SkillLoop.run(...)``, and dispatches loop events to every sink.
  * Periodically writes a ``status.json`` file so external clients
    (``argus-skill daemon-status``) can see what's happening.
  * Honours ``/inject`` while a task is running by buffering text into
    the next reviewer round (passed through a runtime-mutated
    SkillLoopConfig field — light touch, no LoopEngine refactor).
  * Honours ``/skip`` by aborting the current task on the next event
    boundary.
  * Honours ``/stop`` by stopping the worker after the current task.

Provenance: structurally inspired by ArgusBot's daemon_app.py (1081 LOC),
but rewritten for the SkillLoop shape rather than LoopEngine.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.ports import ControlCommand, EventSink
from ..loop import SkillLoop
from .bus import write_status

log = logging.getLogger(__name__)


# Sentinel used to break the worker thread out of queue.get().
class _StopSentinel:
    pass


_STOP = _StopSentinel()


@dataclass
class DaemonConfig:
    status_path: str = ".argus-skill/daemon-status.json"
    status_refresh_seconds: int = 5
    queue_max_size: int = 64
    workdir: str = "."
    inject_buffer_path: str | None = None  # optional file mirroring /inject buffer


@dataclass
class _DaemonState:
    """Mutable runtime state. Protected by ``state_lock``."""
    started_at: str = ""
    daemon_pid: int = 0
    tasks_run: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    current_task: str | None = None
    current_status: str = "idle"  # idle | running | stopping
    last_outcome: dict[str, Any] | None = None
    pending_inject: list[str] = field(default_factory=list)
    skip_current: bool = False
    stop_requested: bool = False


class Daemon:
    """The 7×24 wrapper.

    Construct with a SkillLoop, an EventSink (or composite), and a
    DaemonConfig. Call ``start()`` to spawn the worker thread, then
    feed ``ControlCommand``s by calling ``handle_command(cmd)``.

    For Telegram-fronted operation, plug in:

        daemon = Daemon(loop, sinks)
        channel = CompositeControlChannel([
            TelegramControlChannel(...), LocalBusControlChannel(...),
        ])
        channel.start(daemon.handle_command)
        daemon.start()
        daemon.wait()  # blocks until /stop or signal
    """

    def __init__(
        self,
        *,
        loop: SkillLoop,
        sinks: EventSink,
        config: DaemonConfig | None = None,
    ) -> None:
        self.loop = loop
        self.sinks = sinks
        self.config = config or DaemonConfig()
        self.state = _DaemonState()
        self.state_lock = threading.Lock()
        self._command_queue: queue.Queue = queue.Queue(maxsize=self.config.queue_max_size)
        self._worker: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self.state_lock:
            self.state.started_at = datetime.now(timezone.utc).isoformat()
            self.state.daemon_pid = os.getpid()
            self.state.current_status = "idle"
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._status_thread = threading.Thread(target=self._run_status_writer, daemon=True)
        self._worker.start()
        self._status_thread.start()
        self._emit({"type": "daemon.started",
                    "text": f"argus-skill daemon up (pid={self.state.daemon_pid})"})
        self._write_status()

    def stop(self, *, timeout: float = 30.0) -> None:
        with self.state_lock:
            self.state.stop_requested = True
            self.state.current_status = "stopping"
        self._emit({"type": "daemon.stopping", "text": "shutdown requested"})
        try:
            self._command_queue.put_nowait(_STOP)  # type: ignore[arg-type]
        except queue.Full:
            pass
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
        self._write_status()
        try:
            self.sinks.close()
        except Exception:  # noqa: BLE001
            pass

    def wait(self) -> None:
        """Block the calling thread until the worker has exited."""
        if self._worker is not None:
            self._worker.join()

    # --- command intake ---------------------------------------------------

    def handle_command(self, command: ControlCommand) -> None:
        kind = command.kind
        if kind == "run":
            text = (command.text or "").strip()
            if not text:
                self._emit({"type": "command.error",
                            "text": "/run needs a task description"})
                return
            try:
                self._command_queue.put_nowait(command)
                self._emit({"type": "task.queued",
                            "text": f"queued: {text[:80]}"})
            except queue.Full:
                self._emit({"type": "command.error",
                            "text": "task queue full; drop or wait"})
        elif kind == "inject":
            text = (command.text or "").strip()
            if not text:
                return
            with self.state_lock:
                self.state.pending_inject.append(text)
            self._emit({"type": "command.ack",
                        "text": f"inject buffered ({len(text)} chars)"})
        elif kind == "skip":
            with self.state_lock:
                self.state.skip_current = True
            self._emit({"type": "command.ack", "text": "skip current task"})
        elif kind == "stop":
            self.stop()
        elif kind == "status":
            self._emit({"type": "status.report", "text": self._render_status_short()})
        elif kind == "help":
            self._emit({"type": "help",
                        "text": (
                            "/run <task> /inject <text> /skip /status /stop /help"
                        )})
        else:
            self._emit({"type": "command.unknown", "text": f"unknown command: {kind}"})

    # --- internals --------------------------------------------------------

    def _run_worker(self) -> None:
        while True:
            try:
                command = self._command_queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if isinstance(command, _StopSentinel):
                break
            with self.state_lock:
                if self.state.stop_requested:
                    break
            self._dispatch_run(command)
            with self.state_lock:
                if self.state.stop_requested and self._command_queue.empty():
                    break

    def _dispatch_run(self, command: ControlCommand) -> None:
        task = (command.text or "").strip()
        with self.state_lock:
            self.state.current_task = task
            self.state.current_status = "running"
            self.state.tasks_run += 1
            self.state.skip_current = False
            self.state.last_outcome = None
        self._emit({"type": "task.started", "text": f"running: {task[:120]}"})

        # Wire SkillLoop's per-event callback into our sinks AND check
        # for /skip between events.
        original_on_event = self.loop.on_event
        original_guidance_provider = self.loop.extra_guidance_provider

        def daemon_on_event(event: dict) -> None:
            if original_on_event is not None:
                try:
                    original_on_event(event)
                except Exception:  # noqa: BLE001
                    pass
            self._emit(event)
            with self.state_lock:
                if self.state.skip_current:
                    raise _SkipTaskRequested()

        def drain_pending_inject() -> list[str]:
            with self.state_lock:
                buffered = list(self.state.pending_inject)
                self.state.pending_inject = []
            return buffered

        self.loop.on_event = daemon_on_event
        self.loop.extra_guidance_provider = drain_pending_inject
        outcome_payload: dict[str, Any] = {}
        try:
            outcome = self.loop.run(task, workdir=Path(self.config.workdir))
            outcome_payload = {
                "status": outcome.status,
                "rounds": outcome.round_count,
                "skill_used": outcome.skill_used,
                "skill_distilled": outcome.skill_distilled,
                "reason": outcome.reason,
                "final_message": outcome.final_message[:500],
            }
            with self.state_lock:
                self.state.tasks_done += 1
                self.state.last_outcome = outcome_payload
            self._emit({"type": "task.completed",
                        "text": (
                            f"status={outcome.status} rounds={outcome.round_count} "
                            f"skill={outcome.skill_used or '-'}"
                        )})
        except _SkipTaskRequested:
            with self.state_lock:
                self.state.tasks_failed += 1
                self.state.last_outcome = {"status": "skipped", "task": task}
            self._emit({"type": "task.skipped", "text": f"skipped: {task[:80]}"})
        except Exception as exc:  # noqa: BLE001
            log.exception("task failed")
            with self.state_lock:
                self.state.tasks_failed += 1
                self.state.last_outcome = {
                    "status": "error",
                    "task": task,
                    "exception": f"{type(exc).__name__}: {exc}",
                }
            self._emit({"type": "task.error",
                        "text": f"error: {type(exc).__name__}: {str(exc)[:200]}"})
        finally:
            self.loop.on_event = original_on_event
            self.loop.extra_guidance_provider = original_guidance_provider
            with self.state_lock:
                self.state.current_task = None
                self.state.current_status = "idle"
                self.state.skip_current = False
            self._write_status()

    def _run_status_writer(self) -> None:
        while not self._stop_event.is_set():
            self._write_status()
            self._stop_event.wait(self.config.status_refresh_seconds)
        self._write_status()

    def _write_status(self) -> None:
        try:
            with self.state_lock:
                payload = {
                    "daemon_running": not self.state.stop_requested
                    or self.state.current_status == "running",
                    "daemon_pid": self.state.daemon_pid or os.getpid(),
                    "started_at": self.state.started_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "current_status": self.state.current_status,
                    "current_task": self.state.current_task,
                    "tasks_run": self.state.tasks_run,
                    "tasks_done": self.state.tasks_done,
                    "tasks_failed": self.state.tasks_failed,
                    "queue_size": self._command_queue.qsize(),
                    "pending_inject_count": len(self.state.pending_inject),
                    "last_outcome": self.state.last_outcome,
                }
            write_status(self.config.status_path, payload)
        except Exception:  # noqa: BLE001
            log.exception("status write failed")

    def _render_status_short(self) -> str:
        with self.state_lock:
            current = self.state.current_task or "idle"
            uptime_str = self._format_uptime(self.state.started_at)
            return (
                f"status={self.state.current_status} "
                f"current={current[:60]} "
                f"done={self.state.tasks_done} failed={self.state.tasks_failed} "
                f"queue={self._command_queue.qsize()} "
                f"pending_inject={len(self.state.pending_inject)} "
                f"uptime={uptime_str}"
            )

    @staticmethod
    def _format_uptime(started_at_iso: str) -> str:
        if not started_at_iso:
            return "?"
        try:
            started = datetime.fromisoformat(started_at_iso)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - started
        except (ValueError, TypeError):
            return "?"
        seconds = int(elapsed.total_seconds())
        if seconds < 0:
            seconds = 0
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        if days:
            return f"{days}d{hours}h{minutes}m"
        if hours:
            return f"{hours}h{minutes}m"
        if minutes:
            return f"{minutes}m{seconds}s"
        return f"{seconds}s"

    def _emit(self, event: dict) -> None:
        try:
            self.sinks.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("sink.handle_event raised")


class _SkipTaskRequested(Exception):
    """Raised inside the SkillLoop event callback when /skip is set."""


# --- helpers exposed for tests ------------------------------------------


def make_daemon_event_callback(daemon: Daemon) -> Callable[[dict], None]:
    """Convenience for code that wants to push events into a daemon's
    sinks from outside the loop (e.g. external watcher threads)."""
    return daemon._emit  # noqa: SLF001 — intentional test-only access


__all__ = ["Daemon", "DaemonConfig"]
