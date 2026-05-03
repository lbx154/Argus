"""Telegram bot: long-poll getUpdates, parse text into commands.

Provenance: simplified from ``ArgusBot/codex_autoloop/telegram_control.py``.
Trimmed scope:

  * Kept: ``/run``, ``/status``, ``/inject``, ``/skip``, ``/stop``,
    ``/help``, plain-text-as-inject. These are the commands the
    argus-skill daemon actually understands.
  * Dropped: voice / Whisper transcription, plan-mode menus, attachment
    confirm/cancel flows, /btw, /plan, /review, /criteria,
    show-* commands, callback_query (button) plumbing. They depend on
    ArgusBot internals (planner, attachment policy) we do not vendor.

The poller is thread-based (background daemon thread). It calls
``on_command(TelegramCommand)`` from that thread; the consumer (the
argus-skill daemon main) is responsible for queueing or otherwise being
thread-safe.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TelegramCommand:
    kind: str
    text: str


CommandCallback = Callable[[TelegramCommand], None]
ErrorCallback = Callable[[str], None]


class TelegramCommandPoller:
    """Background thread that long-polls Telegram and dispatches commands.

    Usage::

        poller = TelegramCommandPoller(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
            on_command=lambda c: queue.put(c),
        )
        poller.start()
        ...
        poller.stop()
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        on_command: CommandCallback,
        on_error: ErrorCallback | None = None,
        poll_interval_seconds: int = 2,
        long_poll_timeout_seconds: int = 20,
        plain_text_as_inject: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.on_command = on_command
        self.on_error = on_error
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.long_poll_timeout_seconds = max(1, int(long_poll_timeout_seconds))
        self.plain_text_as_inject = plain_text_as_inject
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = self._fetch_updates()
            except Exception as exc:  # noqa: BLE001 — top-level safety net
                self._emit_error(f"telegram getUpdates unexpected error: {exc}")
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            if updates is None:
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            if not updates:
                continue
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._offset = update_id + 1
                command = parse_command_from_update(
                    update=update,
                    expected_chat_id=self.chat_id,
                    plain_text_as_inject=self.plain_text_as_inject,
                )
                if command is None:
                    continue
                try:
                    self.on_command(command)
                except Exception as exc:  # noqa: BLE001
                    self._emit_error(f"telegram command handler error: {exc}")

    def _fetch_updates(self) -> list[dict[str, Any]] | None:
        query = {"timeout": str(self.long_poll_timeout_seconds)}
        if self._offset is not None:
            query["offset"] = str(self._offset)
        url = self._base_url + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.long_poll_timeout_seconds + 10) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                pass
            self._emit_error(f"telegram getUpdates HTTP {exc.code}: {body[:300]}")
            return None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            self._emit_error(f"telegram getUpdates network error: {exc}")
            return None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._emit_error("telegram getUpdates non-JSON response")
            return None
        if not payload.get("ok"):
            self._emit_error(f"telegram getUpdates api error: {payload.get('description', 'unknown')}")
            return None
        result = payload.get("result", [])
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)


# ---------------------------------------------------------------------------
# Command parsing (module-level for unit-tests)
# ---------------------------------------------------------------------------

def parse_command_from_update(
    *,
    update: dict[str, Any],
    expected_chat_id: str,
    plain_text_as_inject: bool,
) -> TelegramCommand | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    if not _message_matches_chat(message=message, expected_chat_id=expected_chat_id):
        return None
    raw_text = _extract_command_text(message)
    if not raw_text:
        return None
    return parse_command_text(text=raw_text, plain_text_as_inject=plain_text_as_inject)


def parse_command_text(*, text: str, plain_text_as_inject: bool) -> TelegramCommand | None:
    content = _normalize_command_prefix(text.strip())
    if not content:
        return None
    lowered = content.lower()
    # Run a new task.
    if lowered.startswith("/run "):
        return TelegramCommand(kind="run", text=content[len("/run ") :].strip())
    if lowered == "/run":
        return None
    # Inject text into the next round (or buffer for next /run).
    if lowered.startswith("/inject "):
        return TelegramCommand(kind="inject", text=content[len("/inject ") :].strip())
    if lowered == "/inject":
        return None
    if lowered.startswith("/interrupt "):
        return TelegramCommand(kind="inject", text=content[len("/interrupt ") :].strip())
    # Skip the currently-running task.
    if lowered in {"/skip", "/abort"}:
        return TelegramCommand(kind="skip", text="")
    # Stop the daemon entirely.
    if lowered in {"/stop", "/halt", "/daemon-stop", "/shutdown-daemon"}:
        return TelegramCommand(kind="stop", text="")
    # Status / help.
    if lowered in {"/status", "/stat"}:
        return TelegramCommand(kind="status", text="")
    if lowered in {"/help", "/commands"}:
        return TelegramCommand(kind="help", text="")
    if content.startswith("/"):
        # Unknown slash command — treat as a no-op rather than guess.
        return None
    if plain_text_as_inject:
        return TelegramCommand(kind="inject", text=content)
    return None


def _message_matches_chat(*, message: dict[str, Any], expected_chat_id: str) -> bool:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return False
    chat_id = chat.get("id")
    return str(chat_id) == str(expected_chat_id)


def _extract_command_text(message: dict[str, Any]) -> str | None:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text
    caption = message.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption
    return None


def _normalize_command_prefix(text: str) -> str:
    if not text:
        return ""
    if text.startswith("\uff0f"):  # full-width slash
        return "/" + text[1:].lstrip()
    if text.startswith("\u3001"):  # CJK enumeration comma
        return "/" + text[1:].lstrip()
    return text
