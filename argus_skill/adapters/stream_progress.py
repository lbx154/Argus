"""Forward agent CLI stream-json lines as ``engineer.progress`` events.

ArgusBot's ``AgentCliRunner`` invokes its ``event_callback(stream, line)``
once per stdout/stderr line. Stdout, when running with the JSON event
stream (codex's ``--output-format=stream-json`` and friends), produces
one structured event per line — ``thread.started``, ``item.completed``
(with an ``item`` payload), ``turn.completed``, etc.

We tap that callback here to surface live progress in the operator cockpit.
The raw stream lines are also forwarded as-is to the sink so the audit
log keeps everything; the cooked ``engineer.progress`` events are what
the unified cockpit renders in concise mode.

Design choice: mirror ArgusBot's own event ingestion (see
``agent_cli/agent_cli_runner.py::_consume_codex_event``) — we only
inspect ``item.completed`` items here, which is the same beat ArgusBot
treats as "the agent produced something". We deliberately don't try to
stream token-level deltas (codex's stream-json doesn't expose them
reliably across backends).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from ..core.secret_guard import known_secret_values, redact_secrets_text

# Items larger than this are truncated in the cooked progress event so a
# 50KB tool-output dump doesn't blow up the chat scrollback. The full
# payload is still recoverable from the raw ``stream`` lines in the
# outbox.
_PROGRESS_TEXT_LIMIT = 600
_DEFAULT_DELTA_INTERVAL_S = 0.5
_DEFAULT_DELTA_CHARS = 256


def _nonnegative_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _nonnegative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


# Tool names that run a shell command, so their progress reads as a command
# rather than an opaque tool call.
_SHELL_TOOL_NAMES = frozenset({"bash", "shell", "sh", "run", "execute", "terminal"})


def _render_tool_arguments(args: Any) -> str:
    """Render tool arguments as a compact one-line string (never raises)."""
    if isinstance(args, (dict, list)):
        try:
            return json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(args)
    return str(args or "")


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
    """Best-effort text extraction across supported CLI dialects."""
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


def _safe_progress_text(
    text: str,
    *,
    limit: int | None = _PROGRESS_TEXT_LIMIT,
) -> str:
    redacted = redact_secrets_text(text, known_values=known_secret_values())
    return redacted.strip() if limit is None else _truncate(redacted, limit)


def _is_final_delivery_message(text: str) -> bool:
    """True for the Planner's human-readable terminal delivery."""
    return any(
        line.strip().startswith("PROJECT_DONE=")
        for line in (text or "").splitlines()
    )


def _is_structured_role_result(actor: str, text: str) -> bool:
    """Return whether a role message is its machine-consumed verdict footer."""
    role = (actor or "").lower()
    if not (role.startswith("planner") or role.startswith("reviewer")):
        return False
    lines = {
        line.strip().partition("=")[0].upper()
        for line in str(text or "").splitlines()
        if "=" in line
    }
    if role.startswith("reviewer") and {"STATUS", "REASON"} <= lines:
        return True
    if role.startswith("planner") and (
        {"PROJECT_DONE", "REASON"} <= lines or "PLAN_REASON" in lines
    ):
        return True
    # Backward compatibility for an old role turn already in flight.
    try:
        payload = json.loads((text or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


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


def make_stream_progress_callback(
    sink: Any,
    *,
    ledger: Any | None = None,
    min_delta_interval_s: float | None = None,
    min_delta_chars: int | None = None,
) -> Callable[[str, str], None]:
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
    delta_emits: dict[tuple[str, str], tuple[float, int]] = {}
    delta_rendered: dict[tuple[str, str], str] = {}
    # toolCallId -> (name, kind, text) so a ``tool.execution_complete`` failure
    # can name the call that started earlier. Per-callback, like the buffers.
    tool_calls: dict[str, tuple[str, str, str]] = {}
    delta_interval_s = (
        _nonnegative_float_env(
            "ARGUS_SKILL_STREAM_PROGRESS_INTERVAL_S",
            _DEFAULT_DELTA_INTERVAL_S,
        )
        if min_delta_interval_s is None
        else max(0.0, float(min_delta_interval_s))
    )
    delta_chars = (
        _nonnegative_int_env(
            "ARGUS_SKILL_STREAM_PROGRESS_CHARS",
            _DEFAULT_DELTA_CHARS,
        )
        if min_delta_chars is None
        else max(0, int(min_delta_chars))
    )

    def _emit_progress(*, kind: str, text: str, actor: str = "main",
                       replace: bool = False,
                       transient: bool = False,
                       message_id: str | None = None,
                       extra: dict[str, Any] | None = None) -> None:
        if not text:
            return
        final_delivery = (
            kind in {"assistant_message", "agent_message", "message"}
            and _is_final_delivery_message(text)
        )
        agent_speech = kind in {"assistant_message", "agent_message", "message"}
        payload: dict[str, Any] = {
            "type": "engineer.progress",
            "kind": kind,
            "text": _safe_progress_text(
                text,
                limit=None if final_delivery or agent_speech else _PROGRESS_TEXT_LIMIT,
            ),
            "actor": actor,
            "agent_layer": _agent_layer_for_actor(actor),
        }
        if final_delivery:
            payload["final_delivery"] = True
        if extra:
            for key, value in extra.items():
                if value is None or value == "":
                    continue
                if isinstance(value, str):
                    payload[key] = _safe_progress_text(value, limit=360)
                else:
                    payload[key] = value
        if replace:
            payload["replace"] = True
        if transient:
            payload["transient"] = True
        if message_id:
            payload["message_id"] = message_id
        try:
            sink.handle_event(payload)
        except Exception:  # noqa: BLE001
            pass

    def _clear_actor_buffers(actor: str) -> None:
        for key in [k for k in delta_buffers if k[0] == actor]:
            delta_buffers.pop(key, None)
            delta_emits.pop(key, None)
            delta_rendered.pop(key, None)
        # End of turn: any tool call still in flight will never complete, so
        # drop it rather than leaking ids across turns.
        tool_calls.clear()

    def _accumulate_delta(
        *,
        actor: str,
        message_id: str,
        delta: str,
        kind: str,
        buffer_id: str | None = None,
    ) -> None:
        visible_id = message_id.strip()
        key = (actor, (buffer_id or visible_id).strip())
        current = delta_buffers.get(key, "") + delta
        delta_buffers[key] = current
        if not current.strip():
            return
        now = time.monotonic()
        previous_emit = delta_emits.get(key)
        if previous_emit is not None:
            last_at, last_chars = previous_emit
            if (
                now - last_at < delta_interval_s
                and len(current) - last_chars < delta_chars
            ):
                return
        delta_emits[key] = (now, len(current))
        rendered = _truncate(current.strip())
        if delta_rendered.get(key) == rendered:
            return
        delta_rendered[key] = rendered
        _emit_progress(
            kind=kind,
            text=rendered,
            actor=actor,
            replace=True,
            transient=True,
            message_id=visible_id,
        )

    def cb(stream: str, line: str) -> None:
        try:
            sink.handle_stream_line(stream, line)
        except Exception:  # noqa: BLE001 — never let logging crash the runner
            pass
        # Surface the operator-visible hierarchy layers: the Manager front door
        # plus L1 engineer/main, L2 reviewer, L4 planner. Matcher/author/
        # distiller stay hidden because their stdout is protocol traffic or
        # skill-maintenance noise, not live work.
        is_stdout = stream == "stdout" or stream.endswith(".stdout")
        if not is_stdout:
            return
        role = stream.rsplit(".", 1)[0] if "." in stream else ""
        actor = role or "main"
        if not _actor_is_visible(role):
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
            _emit_progress(
                kind=kind,
                text=text,
                actor=actor,
                transient=(
                    kind in {"assistant_message", "agent_message", "message"}
                    and _is_structured_role_result(actor, text)
                ),
                extra=extra,
            )
            return

        # Claude dialect: {"type": "assistant", "message": {"content": [...]}}
        if et == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                text = _extract_text(message)
                if text:
                    _emit_progress(
                        kind="agent_message",
                        text=text,
                        actor=actor,
                        transient=_is_structured_role_result(actor, text),
                    )
            return

        # Cursor CLI dialect: top-level tool lifecycle events carry one typed
        # payload (for example ``readToolCall`` or ``shellToolCall``).
        if et == "tool_call":
            subtype = str(event.get("subtype") or "").strip().lower()
            payload = event.get("tool_call")
            payload = payload if isinstance(payload, dict) else {}
            raw_name, details = next(iter(payload.items()), ("toolCall", {}))
            details = details if isinstance(details, dict) else {}
            name = str(raw_name or "toolCall")
            if name.lower().endswith("toolcall"):
                name = name[:-8] or "tool"
            args = details.get("args", details.get("arguments", {}))
            command = ""
            if isinstance(args, dict):
                command = str(args.get("command") or args.get("cmd") or "").strip()
            rendered = _render_tool_arguments(args)
            is_shell = bool(command) or name.lower() in _SHELL_TOOL_NAMES
            kind = "command_execution" if is_shell else "tool_use"
            text = command or (name + (f": {rendered}" if rendered else ""))
            call_id = str(event.get("call_id") or "").strip()
            if subtype in {"started", "start"}:
                if call_id:
                    tool_calls[call_id] = (name, kind, text)
                _emit_progress(
                    kind=kind,
                    text=text or name,
                    actor=actor,
                    extra={
                        "status": "running",
                        "action_summary": _action_summary(kind, text or name, {
                            "type": kind, "name": name, "command": command,
                        }),
                        "tool_name": name,
                    },
                )
                return
            old_name, old_kind, old_text = tool_calls.pop(
                call_id, (name, kind, text),
            )
            result = details.get("result")
            result = result if isinstance(result, dict) else {}
            exit_code = result.get("exitCode", result.get("exit_code"))
            failed = (
                subtype in {"failed", "error"}
                or details.get("success") is False
                or bool(details.get("isError", False))
                or (isinstance(exit_code, int) and exit_code != 0)
            )
            if not failed:
                return
            item = {
                "type": old_kind,
                "name": old_name,
                "status": "failed",
                "exit_code": exit_code,
                "aggregated_output": result.get("output") or result.get("content") or "",
            }
            if ledger is not None:
                _record_failure_if_any(ledger, old_kind, item)
            _emit_progress(
                kind=old_kind,
                text=old_text or old_name,
                actor=actor,
                extra={
                    "status": "failed",
                    "exit_code": exit_code,
                    "output_excerpt": _extract_output_excerpt(item),
                    "action_summary": _action_summary(old_kind, old_text or old_name, item),
                    "tool_name": old_name,
                },
            )
            return

        # OpenCode dialect: text and completed tool parts are emitted as
        # top-level events with the provider payload under ``part``.
        if et in {"text", "reasoning"}:
            part = event.get("part")
            if isinstance(part, dict):
                part_text = part.get("text")
                if isinstance(part_text, str) and part_text.strip():
                    kind = "reasoning" if et == "reasoning" else "agent_message"
                    text = part_text.strip()
                    _emit_progress(
                        kind=kind,
                        text=text,
                        actor=actor,
                        transient=(
                            kind == "agent_message"
                            and _is_structured_role_result(actor, text)
                        ),
                    )
            return

        if et == "tool_use":
            part = event.get("part")
            if not isinstance(part, dict):
                return
            tool = str(part.get("tool") or "tool").strip()
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            raw_input = state.get("input")
            if isinstance(raw_input, (dict, list)):
                try:
                    rendered_input = json.dumps(raw_input, ensure_ascii=False)
                except (TypeError, ValueError):
                    rendered_input = str(raw_input)
            else:
                rendered_input = str(raw_input or "")
            title = str(state.get("title") or "").strip()
            text = title or (tool + (f": {rendered_input}" if rendered_input else ""))
            metadata = state.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            exit_code = metadata.get("exit")
            status = str(state.get("status") or "").strip()
            adapted_kind = "command_execution" if tool == "bash" else "tool_use"
            adapted_item = {
                "type": adapted_kind,
                "name": tool,
                "command": (
                    raw_input.get("command", "")
                    if tool == "bash" and isinstance(raw_input, dict)
                    else ""
                ),
                "status": (
                    "failed"
                    if status in {"error", "failed"}
                    or (isinstance(exit_code, int) and exit_code != 0)
                    else status
                ),
                "exit_code": exit_code,
                "aggregated_output": state.get("output") or "",
            }
            if ledger is not None:
                _record_failure_if_any(ledger, adapted_kind, adapted_item)
            extra = {
                "status": adapted_item["status"],
                "exit_code": exit_code,
                "output_excerpt": _extract_output_excerpt(adapted_item),
                "action_summary": _action_summary(adapted_kind, text, adapted_item),
            }
            _emit_progress(
                kind=adapted_kind,
                text=text,
                actor=actor,
                extra=extra,
            )
            return

        # Pi dialect: complete assistant messages and tool lifecycle events.
        if et == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                return
            text = _extract_text(message)
            if text:
                message_id = str(
                    message.get("responseId") or message.get("timestamp") or ""
                ).strip()
                _emit_progress(
                    kind="agent_message",
                    text=text,
                    actor=actor,
                    replace=True,
                    transient=_is_structured_role_result(actor, text),
                    message_id=message_id or None,
                )
            return

        if et == "tool_execution_start":
            name = str(event.get("toolName") or "tool").strip()
            args = event.get("args")
            command = (
                str(args.get("command") or "").strip()
                if isinstance(args, dict)
                else ""
            )
            rendered = _render_tool_arguments(args)
            call_id = str(event.get("toolCallId") or "").strip()
            is_shell = bool(command) or name.lower() in _SHELL_TOOL_NAMES
            kind = "command_execution" if is_shell else "tool_use"
            text = command or (name + (f": {rendered}" if rendered else ""))
            if call_id:
                tool_calls[call_id] = (name, kind, text)
            if text:
                _emit_progress(
                    kind=kind,
                    text=text,
                    actor=actor,
                    extra={
                        "status": "running",
                        "action_summary": _action_summary(
                            kind, text, {"type": kind, "name": name, "command": command}
                        ),
                        "tool_name": name,
                    },
                )
            return

        if et == "tool_execution_end":
            call_id = str(event.get("toolCallId") or "").strip()
            name, kind, text = tool_calls.pop(call_id, ("tool", "tool_use", ""))
            failed = bool(event.get("isError", False))
            if not failed:
                return
            result = event.get("result")
            result = result if isinstance(result, dict) else {}
            content = result.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(row.get("text") or "")
                    for row in content
                    if isinstance(row, dict) and row.get("type") == "text"
                )
            item = {
                "type": kind,
                "name": name,
                "status": "failed",
                "aggregated_output": str(content or ""),
            }
            if ledger is not None:
                _record_failure_if_any(ledger, kind, item)
            _emit_progress(
                kind=kind,
                text=text or name,
                actor=actor,
                extra={
                    "status": "failed",
                    "output_excerpt": _extract_output_excerpt(item),
                    "action_summary": _action_summary(kind, text or name, item),
                    "tool_name": name,
                },
            )
            return

        # Copilot dialect: provider-supplied reasoning summaries. Current
        # Copilot builds may emit encrypted/empty reasoning; never surface the
        # opaque reasoningId, only explicit plaintext content.
        if et in {"assistant.reasoning", "assistant.reasoning_delta"}:
            data = event.get("data") or {}
            if not isinstance(data, dict):
                return
            reasoning_id = data.get("reasoningId") or data.get("messageId")
            content = (
                data.get("deltaContent")
                if et == "assistant.reasoning_delta"
                else data.get("content")
            )
            if not isinstance(reasoning_id, str) or not reasoning_id.strip():
                return
            if not isinstance(content, str) or not content:
                return
            visible_id = f"reasoning:{reasoning_id.strip()}"
            if et == "assistant.reasoning_delta":
                _accumulate_delta(
                    actor=actor,
                    message_id=visible_id,
                    buffer_id=visible_id,
                    delta=content,
                    kind="reasoning",
                )
            else:
                key = (actor, visible_id)
                delta_buffers.pop(key, None)
                delta_emits.pop(key, None)
                delta_rendered.pop(key, None)
                _emit_progress(
                    kind="reasoning",
                    text=content.strip(),
                    actor=actor,
                    replace=True,
                    message_id=visible_id,
                )
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
            _accumulate_delta(
                actor=actor,
                message_id=mid,
                delta=delta,
                kind="agent_message",
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
                delta_emits.pop((actor, mid.strip()), None)
                delta_rendered.pop((actor, mid.strip()), None)
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
                transient=_is_structured_role_result(actor, text),
                message_id=mid.strip() if isinstance(mid, str) else None,
            )
            return

        # Copilot tool/command activity. These match codex's
        # ``item.completed`` semantically — surface them as progress so
        # the user sees what the agent is doing between deltas.
        #
        # Current Copilot builds report tool work as
        # ``tool.execution_start`` / ``tool.execution_complete`` (the older
        # ``tool.call`` / ``tool.result`` pair is kept below for compatibility).
        # Without this branch every command the agent ran was invisible to the
        # cockpit: the operator saw reply deltas appear out of nowhere with no
        # sign of the work in between.
        if et == "tool.execution_start":
            data = event.get("data") or {}
            if not isinstance(data, dict):
                return
            name = str(data.get("toolName") or data.get("name") or "tool").strip()
            args = data.get("arguments") or data.get("args") or ""
            command = ""
            if isinstance(args, dict):
                command = str(
                    args.get("command") or args.get("cmd") or args.get("script") or ""
                ).strip()
            rendered = _render_tool_arguments(args)
            call_id = str(data.get("toolCallId") or "").strip()
            is_shell = bool(command) or name.lower() in _SHELL_TOOL_NAMES
            kind = "command_execution" if is_shell else "tool_use"
            text = command if is_shell and command else (
                name + (f": {rendered}" if rendered else "")
            )
            if not text:
                return
            item = {"type": kind, "name": name, "command": command}
            if call_id:
                tool_calls[call_id] = (name, kind, text)
            _emit_progress(
                kind=kind,
                text=text,
                actor=actor,
                extra={
                    "status": "running",
                    "action_summary": _action_summary(kind, text, item),
                    "tool_name": name,
                },
            )
            return

        if et == "tool.execution_complete":
            data = event.get("data") or {}
            if not isinstance(data, dict):
                return
            call_id = str(data.get("toolCallId") or "").strip()
            name, kind, text = tool_calls.pop(
                call_id, ("tool", "tool_use", ""),
            )
            result = data.get("result")
            result = result if isinstance(result, dict) else {}
            success = data.get("success")
            exit_code = result.get("exitCode", result.get("exit_code"))
            failed = success is False or (
                isinstance(exit_code, int) and exit_code != 0
            )
            if not failed:
                # A successful tool call was already reported at start; a second
                # row per call would double the noise without adding signal.
                return
            item = {
                "type": kind,
                "name": name,
                "status": "failed",
                "exit_code": exit_code,
                "aggregated_output": result.get("content") or "",
            }
            if ledger is not None:
                _record_failure_if_any(ledger, kind, item)
            _emit_progress(
                kind=kind,
                text=text or name,
                actor=actor,
                extra={
                    "status": "failed",
                    "exit_code": exit_code,
                    "output_excerpt": _extract_output_excerpt(item),
                    "action_summary": _action_summary(kind, text or name, item),
                    "tool_name": name,
                },
            )
            return

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


class StreamProgressRelay:
    """Reuse ONE ``make_stream_progress_callback`` per ``(sink, ledger)`` pair.

    The callback closes over a per-instance copilot delta-accumulation buffer:
    copilot streams a reply as MANY per-token ``assistant.message_delta`` events
    that the callback folds into a growing string, emitting the accumulated text
    each time so the front-end's ``mergeFragment`` replaces the row in place. That
    buffer MUST survive across stdout lines. A caller that rebuilds the callback
    per line resets the buffer every token, so each token is emitted as a
    standalone fragment — ``mergeFragment`` then newline-appends them and the
    cockpit shows one WORD PER LINE. This relay builds the callback lazily and
    reuses it until the sink or ledger changes (a new mission — exactly when a
    fresh accumulation buffer IS wanted).
    """

    def __init__(
        self,
        *,
        min_delta_interval_s: float | None = None,
        min_delta_chars: int | None = None,
    ) -> None:
        self._cb: Callable[[str, str], None] | None = None
        self._sink: Any = None
        self._ledger: Any = None
        self._min_delta_interval_s = min_delta_interval_s
        self._min_delta_chars = min_delta_chars

    def __call__(self, sink: Any, ledger: Any, stream: str, line: str) -> None:
        if self._cb is None or self._sink is not sink or self._ledger is not ledger:
            self._cb = make_stream_progress_callback(
                sink,
                ledger=ledger,
                min_delta_interval_s=self._min_delta_interval_s,
                min_delta_chars=self._min_delta_chars,
            )
            self._sink = sink
            self._ledger = ledger
        self._cb(stream, line)


# Manager run labels. The Manager front door drives the operator's own turn
# (route classification, the SELF reply, vertical/stage decisions), so its
# stream IS operator-visible work — it is one of the four displayed roles, it
# simply has no L-number. These labels were previously absent from the
# visible-role filter below, which silently dropped every command and tool call
# the Manager made: the cockpit showed a spinner and nothing else.
_ENGINEER_HELPER_ACTOR_PREFIXES = (
    "venue-research",
    "idea-search",
    "research.",
)

_MANAGER_ACTOR_PREFIXES = (
    "manager",
    "simple",
    "chat",
    "router",
    "vertical",
    "stage",
    "domain",
)

# Actors whose stdout is protocol traffic or skill-maintenance noise rather than
# live work the operator wants narrated.
_HIDDEN_ACTOR_PREFIXES = ("matcher", "author", "distill", "scientist", "compaction")


def _actor_is_visible(role: str) -> bool:
    """True when this run label's stream should be narrated to the operator."""
    if not role:
        return True
    lowered = role.lower()
    if lowered.startswith(_HIDDEN_ACTOR_PREFIXES):
        return False
    return lowered.startswith(
        (
            "engineer",
            "main",
            "reviewer",
            "critic",
            "planner",
            *_ENGINEER_HELPER_ACTOR_PREFIXES,
            *_MANAGER_ACTOR_PREFIXES,
        )
    )


def _agent_layer_for_actor(actor: str) -> str:
    actor = (actor or "").lower()
    if actor.startswith("reviewer"):
        return "reviewer"
    if actor.startswith("critic"):
        return "critic"
    if actor.startswith("planner"):
        return "planner"
    if actor.startswith(_MANAGER_ACTOR_PREFIXES):
        return "manager"
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
