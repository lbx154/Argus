"""Per-backend JSON event parsing: turns each backend's stdout event stream
into ``(thread_id, turn_completed, turn_failed, fatal_error)`` updates plus
appended assistant message text. Extracted verbatim from ``agent_cli_runner.py``.
"""

from __future__ import annotations

import json

from .runner_backend import BACKEND_CLAUDE, BACKEND_COPILOT, BACKEND_OPENCODE, BACKEND_PI


class EventConsumerMixin:
    """Dispatches one parsed JSON event to the active backend's consumer."""

    @staticmethod
    def _event_usage_model(event: dict) -> str:
        """Best-effort model identity across Claude-compatible event shapes."""
        candidates: list[object] = [event]
        for key in ("message", "data", "response", "usage"):
            value = event.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        for source in candidates:
            if not isinstance(source, dict):
                continue
            for key in ("model", "model_id", "modelId"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _event_has_tool_activity(event: dict) -> bool:
        """Detect tool use for every streamed CLI dialect.

        The regular subprocess path previously never set
        ``tool_activity_observed`` at all; only Copilot ACP did.  Inspect both
        event names and nested content blocks so Claude ``tool_use``, Codex
        command/file items, Copilot tool deltas, and OpenCode tool parts count.
        """

        event_type = str(event.get("type") or "").strip().casefold()
        if any(
            marker in event_type
            for marker in (
                "tool",
                "command_execution",
                "file_change",
                "web_search",
                "mcp_",
                "function_call",
            )
        ):
            return True

        tool_types = {
            "tool",
            "tool_use",
            "tool_result",
            "tool_call",
            "function_call",
            "command_execution",
            "file_change",
            "web_search",
            "mcp_tool_call",
            "local_shell_call",
        }
        stack: list[object] = [
            event.get("message"),
            event.get("item"),
            event.get("part"),
            event.get("data"),
            event.get("content"),
        ]
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not isinstance(value, dict):
                continue
            nested_type = str(value.get("type") or "").strip().casefold()
            if nested_type in tool_types:
                return True
            if any(
                key in value
                for key in (
                    "tool_calls",
                    "toolCalls",
                    "tool_call_id",
                    "toolCallId",
                    "tool_name",
                    "toolName",
                    "function_call",
                )
            ):
                return True
            stack.extend(value.values())
        return False

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
        if self.backend == BACKEND_OPENCODE:
            return self._consume_opencode_event(
                event=event,
                thread_id=thread_id,
                agent_messages=agent_messages,
                turn_completed=turn_completed,
                turn_failed=turn_failed,
                fatal_error=fatal_error,
            )
        if self.backend == BACKEND_PI:
            return self._consume_pi_event(
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
            text = EventConsumerMixin._extract_claude_message_text(message)
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
    def _consume_opencode_event(
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        session_id = event.get("sessionID")
        if isinstance(session_id, str) and session_id.strip():
            thread_id = session_id

        event_type = str(event.get("type") or "").strip()
        part = event.get("part")
        part = part if isinstance(part, dict) else {}
        if event_type == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                agent_messages.append(text.strip())
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "error":
            turn_failed = True
            error = event.get("error")
            error = error if isinstance(error, dict) else {}
            data = error.get("data")
            data = data if isinstance(data, dict) else {}
            message = data.get("message") or error.get("message") or event.get("message")
            if fatal_error is None and isinstance(message, str) and message.strip():
                fatal_error = message.strip()
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type != "step_finish":
            return thread_id, turn_completed, turn_failed, fatal_error

        reason = str(part.get("reason") or "").strip().lower()
        if reason in {"tool-calls", "tool_calls"}:
            return thread_id, turn_completed, turn_failed, fatal_error
        if reason == "stop":
            turn_completed = True
            return thread_id, turn_completed, turn_failed, fatal_error

        turn_failed = True
        if fatal_error is None:
            fatal_error = f"OpenCode runner reported {reason or 'unknown'}."
        return thread_id, turn_completed, turn_failed, fatal_error

    @staticmethod
    def _consume_pi_event(
        *,
        event: dict,
        thread_id: str | None,
        agent_messages: list[str],
        turn_completed: bool,
        turn_failed: bool,
        fatal_error: str | None,
    ) -> tuple[str | None, bool, bool, str | None]:
        """Consume Pi's documented ``--mode json`` session event stream."""
        event_type = str(event.get("type") or "").strip()
        if event_type == "session":
            session_id = event.get("id")
            if isinstance(session_id, str) and session_id.strip():
                thread_id = session_id.strip()
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "message_end":
            message = event.get("message")
            message = message if isinstance(message, dict) else {}
            if str(message.get("role") or "").strip() != "assistant":
                return thread_id, turn_completed, turn_failed, fatal_error
            text = EventConsumerMixin._extract_claude_message_text(message)
            if text and (not agent_messages or agent_messages[-1] != text):
                agent_messages.append(text)
            stop_reason = str(message.get("stopReason") or "").strip().lower()
            if stop_reason in {"error", "aborted"}:
                turn_failed = True
                if fatal_error is None:
                    detail = str(message.get("errorMessage") or "").strip()
                    fatal_error = detail or f"Pi runner reported {stop_reason}."
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "message_update":
            delta = event.get("assistantMessageEvent")
            delta = delta if isinstance(delta, dict) else {}
            if str(delta.get("type") or "").strip() == "error":
                turn_failed = True
                if fatal_error is None:
                    detail = str(
                        delta.get("errorMessage")
                        or delta.get("message")
                        or delta.get("reason")
                        or ""
                    ).strip()
                    fatal_error = detail or "Pi runner reported a streaming error."
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "auto_retry_end" and event.get("success") is False:
            turn_failed = True
            if fatal_error is None:
                fatal_error = str(event.get("finalError") or "Pi retries exhausted.").strip()
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "extension_error":
            turn_failed = True
            if fatal_error is None:
                fatal_error = str(event.get("error") or "Pi extension failed.").strip()
            return thread_id, turn_completed, turn_failed, fatal_error

        if event_type == "agent_settled" and not turn_failed:
            turn_completed = True
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
    def _retain_json_event(event: dict) -> bool:
        """Keep semantic/final events, not high-frequency transport deltas.

        Every event is still consumed immediately and forwarded to the live
        callback. This only bounds the post-turn ``AgentRunResult`` retained in
        Python memory. Token-bearing deltas are kept for accounting.
        """
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        token_fields = {
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "inputTokens",
            "cachedInputTokens",
            "cacheWriteTokens",
            "outputTokens",
            "reasoningOutputTokens",
        }
        if any(field in event or field in data for field in token_fields):
            return True
        return str(event.get("type") or "") not in {
            "assistant.message_delta",
            "assistant.reasoning_delta",
            "assistant.tool_call_delta",
            "session.background_tasks_changed",
            "tool.execution_partial_result",
            # Pi's partial/full aggregate transport frames are consumed live;
            # ``message_end`` retains the final text + usage once per turn.
            "message_start",
            "message_update",
            "turn_start",
            "turn_end",
            "agent_end",
            "queue_update",
        }
