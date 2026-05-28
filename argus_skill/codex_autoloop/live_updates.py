from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol


_COPILOT_DELTA_BUFFERS: dict[tuple[str, str], str] = {}


def _safe_truncate_markdown(text: str, max_chars: int) -> str:
    """Safely truncate Markdown text without breaking structure.

    Avoids cutting off:
    1. Inside code blocks
    2. In the middle of headers
    3. In the middle of list items

    Args:
        text: Markdown text to truncate
        max_chars: Maximum character count

    Returns:
        Truncated text with continuation marker
    """
    if len(text) <= max_chars:
        return text

    # Check if we're inside a code block at the truncation point
    truncated = text[:max_chars]
    code_block_count = truncated.count('```')

    # If inside a code block (odd number of ```), close it
    if code_block_count % 2 == 1:
        # Find the end of the current line and close the code block
        last_newline = truncated.rfind('\n')
        if last_newline > 0:
            truncated = truncated[:last_newline]
        truncated += '\n```\n\n...（内容被截断）'
        return truncated

    # Not in a code block, try to truncate at a paragraph boundary
    last_double_newline = truncated.rfind('\n\n')
    if last_double_newline > max_chars * 0.7:
        return truncated[:last_double_newline] + '\n\n...（内容被截断）'

    # Try single newline
    last_newline = truncated.rfind('\n')
    if last_newline > max_chars * 0.7:
        return truncated[:last_newline] + '\n\n...（内容被截断）'

    # Try space
    last_space = truncated.rfind(' ')
    if last_space > max_chars * 0.7:
        return truncated[:last_space] + '\n\n...（内容被截断）'

    # Last resort: hard truncate
    return truncated + '\n\n...（内容被截断）'


def extract_agent_message(stream: str, line: str) -> tuple[str, str] | None:
    if not stream.endswith(".stdout"):
        return None
    actor = stream.split(".", 1)[0]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    # Codex format: item.completed with agent_message
    if payload.get("type") == "item.completed":
        item = payload.get("item", {})
        if not isinstance(item, dict):
            return None
        if item.get("type") != "agent_message":
            return None
        text = item.get("text", "")
        if not isinstance(text, str):
            return None
        message = text.strip()
        if message:
            return actor, message
        return None

    # Claude format: assistant message
    if payload.get("type") == "assistant":
        message_obj = payload.get("message", {})
        if not isinstance(message_obj, dict):
            return None
        content = message_obj.get("content", [])
        if not isinstance(content, list):
            return None
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text = c.get("text", "")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return actor, "\n".join(parts)
        return None

    return None


@dataclass(frozen=True)
class LiveUpdateMessage:
    actor: str
    message: str
    replace_pending: bool = False


def extract_stream_report_message(stream: str, line: str) -> LiveUpdateMessage | None:
    extracted = extract_agent_message(stream, line)
    if extracted is not None:
        actor, message = extracted
        return LiveUpdateMessage(actor=actor, message=message, replace_pending=False)

    if not stream.endswith(".stdout"):
        return None
    actor = stream.split(".", 1)[0]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    payload_type = payload.get("type")
    if payload_type == "assistant.message_delta":
        data = payload.get("data", {})
        if not isinstance(data, dict):
            return None
        message_id = data.get("messageId")
        delta = data.get("deltaContent")
        if not isinstance(message_id, str) or not message_id.strip():
            return None
        if not isinstance(delta, str) or not delta:
            return None
        key = (actor, message_id.strip())
        current = _COPILOT_DELTA_BUFFERS.get(key, "")
        current += delta
        _COPILOT_DELTA_BUFFERS[key] = current
        if not current.strip():
            return None
        return LiveUpdateMessage(actor=actor, message=current.strip(), replace_pending=True)

    if payload_type == "assistant.message":
        data = payload.get("data", {})
        if not isinstance(data, dict):
            return None
        content = data.get("content")
        message_id = data.get("messageId")
        if isinstance(message_id, str) and message_id.strip():
            _COPILOT_DELTA_BUFFERS.pop((actor, message_id.strip()), None)
        if not isinstance(content, str):
            return None
        normalized = content.strip()
        if not normalized:
            return None
        return LiveUpdateMessage(actor=actor, message=normalized, replace_pending=True)

    if payload_type == "result":
        _clear_copilot_actor_buffers(actor)
    return None


def _clear_copilot_actor_buffers(actor: str) -> None:
    stale_keys = [key for key in _COPILOT_DELTA_BUFFERS if key[0] == actor]
    for key in stale_keys:
        _COPILOT_DELTA_BUFFERS.pop(key, None)


@dataclass
class TelegramStreamReporterConfig:
    interval_seconds: int = 30
    max_items_per_push: int = 6
    max_chars: int = 3500


class MessageNotifier(Protocol):
    def send_message(self, message: str) -> object:
        ...


class TelegramStreamReporter:
    def __init__(
        self,
        *,
        notifier: MessageNotifier,
        config: TelegramStreamReporterConfig,
        on_error: Callable[[str], None] | None = None,
        channel_name: str = "telegram",
    ) -> None:
        self.notifier = notifier
        self.config = config
        self.on_error = on_error
        self.channel_name = channel_name
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: list[tuple[str, str]] = []
        self._last_seen_by_actor: dict[str, str] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, flush: bool = True) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if flush:
            while self.flush():
                pass
        else:
            with self._lock:
                self._pending.clear()

    def add_message(self, actor: str, message: str) -> None:
        normalized = message.strip()
        if not normalized:
            return
        with self._lock:
            if self._last_seen_by_actor.get(actor) == normalized:
                return
            self._last_seen_by_actor[actor] = normalized
            self._pending.append((actor, normalized))

    def replace_message(self, actor: str, message: str) -> None:
        normalized = message.strip()
        if not normalized:
            return
        with self._lock:
            if self._last_seen_by_actor.get(actor) == normalized:
                return
            self._last_seen_by_actor[actor] = normalized
            self._pending = [item for item in self._pending if item[0] != actor]
            self._pending.append((actor, normalized))

    def flush(self) -> bool:
        with self._lock:
            if not self._pending:
                return False
            batch = self._pending[: self.config.max_items_per_push]
            self._pending = self._pending[self.config.max_items_per_push :]

        # Send each actor's message separately to avoid truncation
        for actor, text in batch:
            message = self._format_single_message(actor, text)
            self.notifier.send_message(message)
        return True

    def _run(self) -> None:
        interval = max(5, int(self.config.interval_seconds))
        while not self._stop_event.wait(interval):
            try:
                self.flush()
            except Exception as exc:
                if self.on_error:
                    self.on_error(f"{self.channel_name} live flush error: {exc}")

    def _format_single_message(self, actor: str, text: str) -> str:
        """Format a single actor's message with markdown."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        trimmed = text.strip()
        # Format as Markdown with actor as bold header
        # Ensure proper newline after bold header for Markdown rendering
        # Increased limit from 420 to 1200 to accommodate JSON outputs
        message_text = f"**{actor}:**\n\n{trimmed[:1200]}"
        rendered = f"[autoloop] live update {now}\n\n{message_text}"

        # Safely truncate if needed, avoiding cutting off Markdown structures
        if len(rendered) <= self.config.max_chars:
            return rendered
        return _safe_truncate_markdown(rendered, self.config.max_chars)
