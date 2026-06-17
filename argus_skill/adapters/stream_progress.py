"""Forward codex/claude/copilot stream-json lines as ``engineer.progress`` events.

ArgusBot's ``CodexRunner`` invokes its ``event_callback(stream, line)``
once per stdout/stderr line. Stdout, when running with the JSON event
stream (codex's ``--output-format=stream-json`` and friends), produces
one structured event per line — ``thread.started``, ``item.completed``
(with an ``item`` payload), ``turn.completed``, etc.

We tap that callback here to surface live progress in the chat REPL.
The raw stream lines are also forwarded as-is to the sink so the audit
log keeps everything; the cooked ``engineer.progress`` events are what
the unified REPL renders in concise mode.

Design choice: mirror ArgusBot's own event ingestion (see
``codex_autoloop/codex_runner.py::_consume_codex_event``) — we only
inspect ``item.completed`` items here, which is the same beat ArgusBot
treats as "the agent produced something". We deliberately don't try to
stream token-level deltas (codex's stream-json doesn't expose them
reliably across backends).
"""
from __future__ import annotations

import json
from typing import Any, Callable

# Items larger than this are truncated in the cooked progress event so a
# 50KB tool-output dump doesn't blow up the chat scrollback. The full
# payload is still recoverable from the raw ``stream`` lines in the
# outbox.
_PROGRESS_TEXT_LIMIT = 600


def _action_summary(kind: str, text: str, item: dict[str, Any]) -> str:
    """Return a concise user-facing action summary; raw text stays in trace."""
    if kind == "command_execution":
        cmd = (text or "").strip()
        for p in ("/bin/bash -lc ", "/bin/bash -c ", "bash -lc ", "bash -c ", "sh -c "):
            if cmd.startswith(p):
                inner = cmd[len(p):].strip()
                if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                    cmd = inner[1:-1]
                else:
                    cmd = inner
                break
        head = cmd.split(None, 1)[0].rsplit("/", 1)[-1] if cmd else ""
        if head in {"pytest", "ruff", "mypy"} or "pytest" in cmd or "ruff check" in cmd:
            return "running validation"
        if head in {"python", "python3"} and any(
            marker in cmd for marker in (" -m pytest", " -m ruff", " -m mypy", "compileall")
        ):
            return "running validation"
        if head in {"rg", "grep", "find", "ls", "sed", "awk", "head", "tail", "cat"}:
            return "inspecting project state"
        if head == "git":
            return "checking repository state"
        if head in {"ssh", "curl"}:
            return "checking external/runtime state"
        return "running project command"
    if kind == "file_change":
        changes = item.get("changes") or []
        return "editing project files" if isinstance(changes, list) and changes else "preparing file changes"
    if kind == "tool_use":
        return "using a tool"
    if kind == "reasoning":
        return "reasoning about next step"
    if kind in {"assistant_message", "agent_message", "message"}:
        return "reporting progress"
    return "working"


def _extract_text(item: dict[str, Any]) -> str:
    """Best-effort text extraction across codex/claude/copilot dialects."""
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    # Claude wraps content as a list of {"type": "text"|"tool_use", "text": ...}
    content = item.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for piece in content:
            if isinstance(piece, dict):
                t = piece.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        if parts:
            return "\n".join(parts).strip()
    if isinstance(content, str) and content.strip():
        return content.strip()
    # Codex command_execution / tool_use items keep the command in
    # 'command' / 'name'.
    cmd = item.get("command") or item.get("name")
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip()
    return ""


def _truncate(s: str, n: int = _PROGRESS_TEXT_LIMIT) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _shell_tool_bucket(command: str) -> str:
    """Group shell commands by leading binary so 'apply_patch'-style
    repeated failures cluster (e.g. all ``git ...`` invocations under
    ``shell:git``) without one-bucket-per-unique-command explosion.
    """
    cmd = (command or "").strip()
    # Codex wraps as /bin/bash -lc 'real cmd'; peel one quoting layer.
    for p in ("/bin/bash -lc ", "/bin/bash -c ", "bash -lc ", "bash -c ", "sh -c "):
        if cmd.startswith(p):
            inner = cmd[len(p):].strip()
            if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
                cmd = inner[1:-1]
            else:
                cmd = inner
            break
    leader = cmd.split(None, 1)[0] if cmd else ""
    # Strip path so ``/usr/bin/python3`` and ``python3`` share a bucket.
    leader = leader.rsplit("/", 1)[-1] or "shell"
    return f"shell:{leader}"


def _record_failure_if_any(ledger: Any, kind: str, item: dict[str, Any]) -> None:
    """Inspect a codex ``item.completed`` payload and, when it represents
    a failed beat, record it in the ledger.

    Failure semantics observed in codex stream-json:
      * ``command_execution``: ``status == "failed"`` AND/OR
        non-zero ``exit_code``; ``aggregated_output`` carries stderr.
      * ``file_change``: ``status == "failed"``; ``changes`` carries
        path metadata. ``apply_patch``-style failures land here.
      * ``tool_use``: rare in codex CLI but follow the same status
        contract.

    The ledger silently no-ops on success or unrecognised shapes so this
    is safe to call on every beat.
    """
    if not isinstance(item, dict):
        return
    status = str(item.get("status") or "").lower()
    exit_code = item.get("exit_code")
    failed = (
        status == "failed"
        or (isinstance(exit_code, int) and exit_code not in (0, None))
    )
    if not failed:
        return

    err = ""
    detail = ""
    bucket = ""
    if kind == "command_execution":
        cmd = str(item.get("command") or "").strip()
        bucket = _shell_tool_bucket(cmd)
        detail = cmd
        err = str(item.get("aggregated_output") or "").strip() or f"exit_code={exit_code}"
    elif kind == "file_change":
        bucket = "apply_patch"
        changes = item.get("changes") or []
        if isinstance(changes, list):
            paths = ", ".join(
                str(c.get("path", "?")) for c in changes if isinstance(c, dict)
            )
            detail = paths
        err = (
            str(item.get("aggregated_output") or item.get("error") or "").strip()
            or f"file_change failed (status={status})"
        )
    elif kind == "tool_use":
        bucket = "tool:" + str(item.get("name") or "unknown")
        detail = str(item.get("name") or "")
        err = str(item.get("aggregated_output") or item.get("error") or "").strip() or f"status={status}"
    else:
        return

    try:
        ledger.record(bucket, err, detail=detail)
    except Exception:  # noqa: BLE001 — ledger failures must never crash the stream
        pass


def make_stream_progress_callback(sink: Any, *, ledger: Any | None = None) -> Callable[[str, str], None]:
    """Return an ``(stream, line) -> None`` callback that:

      * always forwards the raw line to ``sink.handle_stream_line`` so
        the JSONL outbox keeps the verbatim audit trail, and
      * additionally emits a structured ``engineer.progress`` event
        (via ``sink.handle_event``) every time the JSON line represents
        an ``item.completed`` beat — i.e. an agent message, reasoning
        block, or tool call.

    Copilot dialect: copilot's stream-json emits incremental
    ``assistant.message_delta`` events (one per token chunk) keyed by
    ``messageId``, then a final ``assistant.message`` with the full
    text. We accumulate deltas into a per-callback buffer keyed by
    ``(actor, messageId)`` and emit ``engineer.progress`` events with
    ``replace=True`` so the renderer can replace the previous chunk in
    place rather than appending a new line per token. ``result`` and
    the final ``assistant.message`` events flush + reset the buffer
    for that actor.

    Buffers are scoped to **this callback instance** rather than module
    globals so multiple daemons / tests don't cross-talk.
    """
    # (actor, message_id) -> accumulated text. Per-callback to avoid
    # cross-task leakage. Mirror of ArgusBot's ``_COPILOT_DELTA_BUFFERS``
    # but instance-scoped.
    delta_buffers: dict[tuple[str, str], str] = {}

    def _emit_progress(*, kind: str, text: str, actor: str = "main",
                       replace: bool = False,
                       message_id: str | None = None,
                       extra: dict[str, Any] | None = None) -> None:
        if not text:
            return
        payload: dict[str, Any] = {
            "type": "engineer.progress",
            "kind": kind,
            "text": _truncate(text),
            "actor": actor,
            "agent_layer": _agent_layer_for_actor(actor),
        }
        if extra:
            for key, value in extra.items():
                if value is None or value == "":
                    continue
                if isinstance(value, str):
                    payload[key] = _truncate(value, 360)
                else:
                    payload[key] = value
        if replace:
            payload["replace"] = True
        if message_id:
            payload["message_id"] = message_id
        try:
            sink.handle_event(payload)
        except Exception:  # noqa: BLE001
            pass

    def _clear_actor_buffers(actor: str) -> None:
        for key in [k for k in delta_buffers if k[0] == actor]:
            delta_buffers.pop(key, None)

    def cb(stream: str, line: str) -> None:
        try:
            sink.handle_stream_line(stream, line)
        except Exception:  # noqa: BLE001 — never let logging crash the runner
            pass
        # Surface the operator-visible hierarchy layers:
        #   L1 engineer/main, L2 reviewer, L4 planner.
        # Keep matcher/scientist/distiller hidden because their stdout is
        # protocol traffic or skill-maintenance noise, not live work.
        is_stdout = stream == "stdout" or stream.endswith(".stdout")
        if not is_stdout:
            return
        role = stream.rsplit(".", 1)[0] if "." in stream else ""
        actor = role or "main"
        if role and not (
            role == "engineer"
            or role == "main"
            or role == "reviewer"
            or role.startswith("engineer")
            or role.startswith("main")
            or role.startswith("reviewer")
            or role.startswith("critic")
            or role.startswith("planner")
        ):
            return
        line = line.strip()
        if not line or line[0] not in "{[":
            return
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        et = str(event.get("type") or "").strip()

        # Codex / copilot dialect: {"type": "item.completed", "item": {...}}
        if et == "item.completed":
            item = event.get("item") or {}
            if not isinstance(item, dict):
                return
            kind = str(item.get("type") or "").strip() or "message"
            # Failure side-channel: codex marks failed beats with
            # ``status == "failed"``. Tally these into the ledger so the
            # engineer prompt can interrupt blind-retry loops.
            if ledger is not None:
                _record_failure_if_any(ledger, kind, item)
            text = _extract_text(item)
            if not text:
                return
            extra: dict[str, Any] = {}
            status = item.get("status")
            exit_code = item.get("exit_code")
            if status is not None:
                extra["status"] = status
            if exit_code is not None:
                extra["exit_code"] = exit_code
            output_excerpt = _extract_output_excerpt(item)
            if output_excerpt:
                extra["output_excerpt"] = output_excerpt
            extra["action_summary"] = _action_summary(kind, text, item)
            _emit_progress(kind=kind, text=text, actor=actor, extra=extra)
            return

        # Claude dialect: {"type": "assistant", "message": {"content": [...]}}
        if et == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                text = _extract_text(message)
                if text:
                    _emit_progress(kind="agent_message", text=text, actor=actor)
            return

        # Copilot dialect: incremental ``assistant.message_delta`` events
        # keyed by messageId, then a final ``assistant.message``.
        if et == "assistant.message_delta":
            data = event.get("data") or {}
            if not isinstance(data, dict):
                return
            mid = data.get("messageId")
            delta = data.get("deltaContent")
            if not isinstance(mid, str) or not mid.strip():
                return
            if not isinstance(delta, str) or not delta:
                return
            key = (actor, mid.strip())
            current = delta_buffers.get(key, "") + delta
            delta_buffers[key] = current
            if not current.strip():
                return
            _emit_progress(
                kind="agent_message",
                text=current.strip(),
                actor=actor,
                replace=True,
                message_id=mid.strip(),
            )
            return

        if et == "assistant.message":
            data = event.get("data") or {}
            if not isinstance(data, dict):
                return
            content = data.get("content")
            mid = data.get("messageId")
            if isinstance(mid, str) and mid.strip():
                # Final message arrived — drop the accumulated buffer
                # for this messageId so we don't double-emit on resume.
                delta_buffers.pop((actor, mid.strip()), None)
            if not isinstance(content, str):
                return
            text = content.strip()
            if not text:
                return
            _emit_progress(
                kind="agent_message",
                text=text,
                actor=actor,
                replace=True,
                message_id=mid.strip() if isinstance(mid, str) else None,
            )
            return

        # Copilot tool/command activity. These match codex's
        # ``item.completed`` semantically — surface them as progress so
        # the user sees what the agent is doing between deltas.
        if et == "tool.call":
            data = event.get("data") or {}
            if isinstance(data, dict):
                name = data.get("name") or data.get("tool")
                args = data.get("arguments") or data.get("args") or ""
                if isinstance(args, (dict, list)):
                    try:
                        args = json.dumps(args, ensure_ascii=False)
                    except (TypeError, ValueError):
                        args = str(args)
                text = (str(name) + (": " + str(args) if args else "")).strip()
                if text:
                    _emit_progress(kind="tool_use", text=text, actor=actor)
            return

        if et == "tool.result":
            data = event.get("data") or {}
            if isinstance(data, dict):
                content = data.get("content") or data.get("output") or ""
                if isinstance(content, (dict, list)):
                    try:
                        content = json.dumps(content, ensure_ascii=False)
                    except (TypeError, ValueError):
                        content = str(content)
                text = str(content).strip()
                if text:
                    _emit_progress(kind="tool_result", text=text, actor=actor)
            return

        # Copilot end-of-turn signal. Clear actor buffers so the next
        # message_id starts clean even if a prior one never received a
        # final ``assistant.message``.
        if et == "result":
            _clear_actor_buffers(actor)
            return

    return cb


def _agent_layer_for_actor(actor: str) -> str:
    actor = (actor or "").lower()
    if actor.startswith("reviewer"):
        return "reviewer"
    if actor.startswith("critic"):
        return "critic"
    if actor.startswith("planner"):
        return "planner"
    return "engineer"


def _extract_output_excerpt(item: dict[str, Any]) -> str:
    raw = (
        item.get("aggregated_output")
        or item.get("output")
        or item.get("error")
        or ""
    )
    if not isinstance(raw, str):
        raw = str(raw)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""
    return _truncate(" | ".join(lines[:3]), 360)


__all__ = ["make_stream_progress_callback"]
