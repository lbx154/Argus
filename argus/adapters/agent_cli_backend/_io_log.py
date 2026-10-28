"""Agent I/O logging: per-call JSONL event log plus raw stream batching.

Every ``AgentCliBackend.run_exec`` call optionally persists a durable JSONL
trail (start/stream/complete/usage/budget events) alongside the live
event-callback forwarding used for progress display. This module owns that
concern: the env-configurable knobs (batch size, flush interval), the
low-level JSONL append helpers, and :class:`AgentIOLogger`, which encapsulates
the per-call "current io context" state (buffer, locks, external callback)
that used to live directly on ``AgentCliBackend``.

The raw transcript names a call once: its ``agent.io.start`` row carries the
identity (run label, backend, model, prompt); each frame after it is only
``call_id``, ``stream``, ``line`` and ``ts``. Older transcripts repeated the
identity on every row.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ...agent_cli.agent_cli_runner import AgentCliRunner
from ...core.event_catalog import normalize_event_envelope
from ...core.secret_guard import redact_secrets_record, redact_secrets_text

_AGENT_IO_BATCH_BYTES_ENV = "ARGUS_SKILL_AGENT_IO_BATCH_BYTES"
_AGENT_IO_FLUSH_INTERVAL_ENV = "ARGUS_SKILL_AGENT_IO_FLUSH_INTERVAL_S"
# ``agent_io.jsonl`` is the verbatim provider transcript — a DEBUG artifact.
# ``events.jsonl`` is the authoritative history, and it already rotates. This
# one did not, so a long-lived session grew it without bound: measured 6.1 GiB
# in a single session and 33 GiB across sessions on one box. Bound it as a ring
# (cap x (keep + 1)) so the recent window stays debuggable without the daemon
# slowly filling the disk.
_AGENT_IO_MAX_BYTES_ENV = "ARGUS_SKILL_AGENT_IO_MAX_BYTES"
_AGENT_IO_KEEP_ENV = "ARGUS_SKILL_AGENT_IO_KEEP"
_DEFAULT_AGENT_IO_MAX_BYTES = 128 * 1024 * 1024
_DEFAULT_AGENT_IO_KEEP = 2
_RAW_TRANSCRIPT_NAME = "agent_io.jsonl"

# These calls interpret control state; their protocol stays in the diagnostic
# transcript, rather than appearing as research progress for the operator.
_INTERNAL_CONTROL_RUN_LABELS = frozenset({
    "planner.acceptance_dependencies", "reviewer_control",
})


def raw_transcript_path(log_path: "Path | None") -> "Path | None":
    """Sibling of the history log that holds the verbatim provider transcript.

    One definition so the start record and the raw stream cannot drift onto
    different files.
    """
    return None if log_path is None else Path(log_path).with_name(_RAW_TRANSCRIPT_NAME)

_DEFAULT_AGENT_IO_BATCH_BYTES = 64 * 1024
_DEFAULT_AGENT_IO_FLUSH_INTERVAL_S = 0.5
_PROGRESS_STREAM_MARKERS = (
    '"item.completed"',
    '"assistant.message_delta"',
    '"assistant.message"',
    '"assistant.reasoning"',
    '"type":"assistant"',
    '"type": "assistant"',
    '"tool.call"',
    '"tool.result"',
    # Current Copilot builds report tool work with these two events rather than
    # ``tool.call`` / ``tool.result``. Omitting them dropped every command the
    # agent ran before it reached the progress parser, so the cockpit showed a
    # spinner and nothing else while real work was happening.
    '"tool.execution_start"',
    '"tool.execution_complete"',
    '"type":"result"',
    '"type": "result"',
    '"type":"text"',
    '"type": "text"',
    '"type":"tool_use"',
    '"type": "tool_use"',
    '"type":"step_start"',
    '"type": "step_start"',
    '"type":"step_finish"',
    '"type": "step_finish"',
    '"type":"reasoning"',
    '"type": "reasoning"',
    # Pi ``--mode json`` event names.
    '"type":"message_end"',
    '"type": "message_end"',
    '"type":"tool_execution_start"',
    '"type": "tool_execution_start"',
    '"type":"tool_execution_end"',
    '"type": "tool_execution_end"',
)


# What the transcript leaves out beyond the frames the runner already drops as
# transport (``retain_json_event``: Copilot's ``*_delta`` and growing partial
# tool results, Pi's per-token updates and aggregate turn/agent ends). Pi's
# partial tool result is repeated whole by ``tool_execution_end``, Copilot's
# message start only opens a run of deltas; the rest is Copilot housekeeping
# that says nothing of what the agent did. Codex and Claude streams carry
# neither kind. Live progress still sees every frame.
_UNSAVED_FRAMES = frozenset({
    "tool_execution_update",
    "assistant.message_start", "assistant.idle",
    "session.mcp_servers_loaded", "session.mcp_server_status_changed",
    "session.mcp_server_tools_loaded", "session.tools_updated",
    "model.call_start", "model.call_finished", "model.call_final_result",
})


def _saved_frame(line: str, prompt: str) -> bool:
    try:
        event = json.loads(line)
    except ValueError:
        return True
    if not isinstance(event, dict):
        return True
    if event.get("type") == "user.message":
        # agent.io.start already holds the prompt the CLI echoes back.
        data = event.get("data")
        return not (isinstance(data, dict) and data.get("content") == prompt)
    return event.get("type") not in _UNSAVED_FRAMES and AgentCliRunner.retain_json_event(event)


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.01, float(raw))
    except ValueError:
        return default


def _needed_for_live_progress(stream: str, line: str) -> bool:
    canonical_stream = stream.rsplit(".", 1)[-1]
    if canonical_stream != "stdout":
        return False
    raw = str(line or "").strip()
    return raw.startswith("{") and any(
        marker in raw for marker in _PROGRESS_STREAM_MARKERS
    )


def _command_metadata(command: Any) -> list[str]:
    """Preserve argv once without duplicating a Copilot ``-p`` prompt body."""
    values = [str(value) for value in (command or [])]
    out: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        out.append(value)
        if value in {"-p", "--prompt"} and index + 1 < len(values):
            out.append("<prompt>")
            index += 2
            continue
        index += 1
    return out


def _jsonl_append(path: Path, row: dict[str, Any], lock: threading.Lock) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        return
    try:
        with lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        return


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _roll_agent_io_log(path: Path) -> None:
    """Rotate ``path`` once it exceeds the cap, keeping a bounded ring.

    ``path`` -> ``path.1`` -> ... -> ``path.<keep>``; the oldest generation is
    dropped. Unlike ``events.jsonl`` (the authoritative history, which retains
    every generation) this transcript is reproducible debug output, so bounding
    total disk is worth more than infinite retention. Best-effort: a failed
    rotation must never break the provider call that is trying to log.
    """
    max_bytes = _positive_int_env(_AGENT_IO_MAX_BYTES_ENV, _DEFAULT_AGENT_IO_MAX_BYTES)
    if max_bytes <= 0:
        return
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    keep = _positive_int_env(_AGENT_IO_KEEP_ENV, _DEFAULT_AGENT_IO_KEEP)
    try:
        if keep <= 0:
            path.unlink(missing_ok=True)
            return
        oldest = path.with_name(f"{path.name}.{keep}")
        oldest.unlink(missing_ok=True)
        for index in range(keep - 1, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            if src.exists():
                src.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        return


def _jsonl_append_lines(
    path: Path,
    lines: list[str],
    lock: threading.Lock,
) -> None:
    if not lines:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(line + "\n" for line in lines)
    except Exception:  # noqa: BLE001
        return
    try:
        with lock:
            _roll_agent_io_log(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(payload)
    except OSError:
        return


class AgentIOLogger:
    """Owns the per-``AgentCliBackend`` I/O logging state across one call.

    ``AgentCliBackend`` is serial by contract (one call at a time). The
    "current call" context and its raw-stream buffer are still guarded by an
    ``RLock`` shared with the plain append lock, so a frame delivered from
    another thread never sees a half-switched call.
    """

    def __init__(self, *, external_event_callback: Any | None = None) -> None:
        self.external_event_callback = external_event_callback
        self.io_log_lock = threading.Lock()
        self.io_context_lock = threading.RLock()
        self.io_context: dict[str, Any] | None = None

    def start_call(
        self,
        *,
        call_id: str,
        run_label: str,
        log_path: Path | None,
        prompt: str,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "call_id": call_id,
            "run_label": run_label,
            "raw_log_path": (
                str(raw_transcript_path(log_path)) if log_path is not None else ""
            ),
            "original_prompt": prompt,
            "buffer": [],
            "buffer_bytes": 0,
            "last_flush": time.monotonic(),
        }
        with self.io_context_lock:
            self.io_context = context
        return context

    def log(
        self,
        path: Path | None,
        row: dict[str, Any],
        *,
        known_secret_values: Any,
    ) -> None:
        if path is None:
            return
        safe_row = redact_secrets_record(
            normalize_event_envelope(row),
            known_values=known_secret_values,
        )
        _jsonl_append(path, safe_row, self.io_log_lock)

    def buffer_stream(
        self,
        context: dict[str, Any],
        path: Path,
        row: dict[str, Any],
    ) -> None:
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        flush_lines: list[str] = []
        now = time.monotonic()
        with self.io_context_lock:
            if self.io_context is not context:
                return
            buffer = context["buffer"]
            buffer.append(line)
            context["buffer_bytes"] += len(line.encode("utf-8")) + 1
            if (
                context["buffer_bytes"]
                >= _positive_int_env(
                    _AGENT_IO_BATCH_BYTES_ENV,
                    _DEFAULT_AGENT_IO_BATCH_BYTES,
                )
                or now - context["last_flush"]
                >= _positive_float_env(
                    _AGENT_IO_FLUSH_INTERVAL_ENV,
                    _DEFAULT_AGENT_IO_FLUSH_INTERVAL_S,
                )
            ):
                flush_lines = list(buffer)
                buffer.clear()
                context["buffer_bytes"] = 0
                context["last_flush"] = now
        if flush_lines:
            _jsonl_append_lines(path, flush_lines, self.io_log_lock)

    def close(self, call_id: str) -> None:
        lines: list[str] = []
        path: Path | None = None
        with self.io_context_lock:
            context = self.io_context
            if context is None or str(context.get("call_id") or "") != call_id:
                return
            raw_path = str(context.get("raw_log_path") or "")
            if raw_path:
                path = Path(raw_path)
            lines = list(context.get("buffer") or [])
            self.io_context = None
        if path is not None and lines:
            _jsonl_append_lines(path, lines, self.io_log_lock)

    def stream_event_callback(
        self,
        stream: str,
        line: str,
        *,
        known_secret_values: Any,
    ) -> None:
        with self.io_context_lock:
            context = self.io_context
        ctx = context or {}
        log_path = str(ctx.get("raw_log_path") or "")
        persist_raw = bool(log_path) and _saved_frame(line, str(ctx.get("original_prompt") or ""))
        run_label = str(ctx.get("run_label") or stream.rsplit(".", 1)[0])
        forward_live = (
            self.external_event_callback is not None
            and run_label not in _INTERNAL_CONTROL_RUN_LABELS
            and _needed_for_live_progress(stream, line)
        )
        if not persist_raw and not forward_live:
            return
        canonical_stream = stream.rsplit(".", 1)[-1]
        if canonical_stream not in {"stdout", "stderr"}:
            canonical_stream = "stdout"
        safe_line = redact_secrets_text(
            line,
            known_values=known_secret_values,
        )
        if persist_raw:
            # A raw path exists only inside a call, so ``ctx`` is its context.
            self.buffer_stream(ctx, Path(log_path), {
                "call_id": ctx["call_id"], "stream": canonical_stream,
                "line": safe_line, "ts": time.time(),
            })
        if forward_live and self.external_event_callback is not None:
            self.external_event_callback(stream, safe_line)
