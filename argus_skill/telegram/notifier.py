"""Telegram bot: outbound notifier (sendMessage + sendDocument).

Provenance: trimmed from ``ArgusBot/codex_autoloop/telegram_notifier.py``.
Kept: text + document send, chunking long messages, typing pulse during
long operations. Dropped: live-message editing, photo/video special
casing — we use ``sendDocument`` for everything non-text.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

ErrorCallback = Callable[[str], None]


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout_seconds: int = 10
    typing_enabled: bool = True
    typing_interval_seconds: int = 4
    notify_event_types: set[str] = field(default_factory=lambda: {
        "task.queued",
        "task.started",
        "task.completed",
        "task.skipped",
        "loop.start",
        "match.info",
        "scientist.start",
        "round.start",
        "review.done",
        "checks.done",
        "skill.writeback",
        "loop.done",
        "task.error",
        "daemon.started",
        "daemon.stopping",
        "help",
        "status.report",
        "command.ack",
        "command.error",
        "command.unknown",
    })


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, on_error: ErrorCallback | None = None) -> None:
        self.config = config
        self.on_error = on_error
        base = f"https://api.telegram.org/bot{config.bot_token}"
        self.send_message_url = f"{base}/sendMessage"
        self.send_chat_action_url = f"{base}/sendChatAction"
        self.send_document_url = f"{base}/sendDocument"
        self._typing_stop = threading.Event()
        self._typing_thread: threading.Thread | None = None

    # --- public surface ---------------------------------------------------

    def send_message(self, message: str) -> bool:
        chunks = _split_telegram_text(message, limit=3900)
        if not chunks:
            return True
        sent_all = True
        for chunk in chunks:
            payload = {
                "chat_id": self.config.chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if not self._post_form(self.send_message_url, payload):
                sent_all = False
        return sent_all

    def send_local_file(self, path: str | Path, *, caption: str = "") -> bool:
        file_path = Path(path)
        if not file_path.exists():
            self._emit_error(f"Telegram local file missing: {file_path}")
            return False
        try:
            file_bytes = file_path.read_bytes()
        except OSError as exc:
            self._emit_error(f"Telegram local file read failed: {exc}")
            return False
        boundary = f"----argusskill{uuid4().hex}"
        body = bytearray()
        body.extend(_multipart_text_part(boundary, "chat_id", self.config.chat_id))
        if caption:
            body.extend(_multipart_text_part(boundary, "caption", caption[:1024]))
        body.extend(
            _multipart_file_part(boundary, "document", file_path.name, file_bytes)
        )
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(
            self.send_document_url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=max(30, self.config.timeout_seconds)):
                return True
        except urllib.error.URLError as exc:
            self._emit_error(f"Telegram sendDocument network error: {exc}")
            return False

    def notify_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "task.started":
            self._start_typing()
        elif event_type in {"loop.done", "task.error", "daemon.stopping"}:
            self._stop_typing()
        if event_type not in self.config.notify_event_types:
            return
        message = format_event_message(event)
        if message:
            self.send_message(message)

    def send_typing(self) -> None:
        payload = {"chat_id": self.config.chat_id, "action": "typing"}
        self._post_form(self.send_chat_action_url, payload)

    def close(self) -> None:
        self._stop_typing()

    # --- internals --------------------------------------------------------

    def _start_typing(self) -> None:
        if not self.config.typing_enabled:
            return
        if self._typing_thread is not None and self._typing_thread.is_alive():
            return
        self._typing_stop.clear()
        self._typing_thread = threading.Thread(target=self._typing_loop, daemon=True)
        self._typing_thread.start()

    def _stop_typing(self) -> None:
        self._typing_stop.set()
        thread = self._typing_thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._typing_thread = None

    def _typing_loop(self) -> None:
        while not self._typing_stop.is_set():
            self.send_typing()
            self._typing_stop.wait(self.config.typing_interval_seconds)

    def _post_form(self, url: str, payload: dict[str, Any]) -> bool:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                pass
            self._emit_error(f"Telegram HTTP {exc.code}: {body[:300]}")
            return False
        except urllib.error.URLError as exc:
            self._emit_error(f"Telegram network error: {exc}")
            return False
        try:
            response_payload = json.loads(raw)
        except json.JSONDecodeError:
            self._emit_error("Telegram non-JSON response")
            return False
        if not response_payload.get("ok"):
            self._emit_error(
                f"Telegram api error: {response_payload.get('description', 'unknown')}"
            )
            return False
        return True

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)


def format_event_message(event: dict[str, Any]) -> str:
    """Render a structured event as a short Telegram-friendly string."""
    kind = str(event.get("type", "?"))
    text = str(event.get("text", "")).strip()
    if not text:
        return f"[{kind}]"
    # 200-char cap per event line; long texts get summarized client-side.
    if len(text) > 200:
        text = text[:200].rstrip() + "…"
    prefix = f"[{kind}]"
    return f"{prefix} {text}"


def _split_telegram_text(text: str, *, limit: int) -> list[str]:
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Try to split on a newline to keep messages readable.
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _multipart_text_part(boundary: str, field: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file_part(
    boundary: str, field: str, filename: str, content: bytes
) -> bytes:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = b"\r\n"
    return head + content + tail
