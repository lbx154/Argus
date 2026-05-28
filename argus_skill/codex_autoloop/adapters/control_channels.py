from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ..core.ports import CommandHandler, ControlCommand
from ..daemon_bus import JsonlCommandBus
from ..feishu_adapter import FeishuCommandPoller
from ..telegram_control import TelegramCommandPoller

ErrorHandler = Callable[[str], None]


class TelegramControlChannel:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        on_error: ErrorHandler | None = None,
        poll_interval_seconds: int = 2,
        long_poll_timeout_seconds: int = 20,
        plain_text_as_inject: bool = True,
        whisper_enabled: bool = True,
        whisper_api_key: str | None = None,
        whisper_model: str = "whisper-1",
        whisper_base_url: str = "https://api.openai.com/v1",
        whisper_timeout_seconds: int = 90,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.on_error = on_error
        self.poll_interval_seconds = poll_interval_seconds
        self.long_poll_timeout_seconds = long_poll_timeout_seconds
        self.plain_text_as_inject = plain_text_as_inject
        self.whisper_enabled = whisper_enabled
        self.whisper_api_key = whisper_api_key
        self.whisper_model = whisper_model
        self.whisper_base_url = whisper_base_url
        self.whisper_timeout_seconds = whisper_timeout_seconds
        self._poller: TelegramCommandPoller | None = None

    def start(self, on_command: CommandHandler) -> None:
        if self._poller is not None:
            return

        def _forward(command) -> None:
            on_command(ControlCommand(kind=command.kind, text=command.text, source="telegram"))

        self._poller = TelegramCommandPoller(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            on_command=_forward,
            on_error=self.on_error,
            poll_interval_seconds=self.poll_interval_seconds,
            long_poll_timeout_seconds=self.long_poll_timeout_seconds,
            plain_text_as_inject=self.plain_text_as_inject,
            whisper_enabled=self.whisper_enabled,
            whisper_api_key=self.whisper_api_key,
            whisper_model=self.whisper_model,
            whisper_base_url=self.whisper_base_url,
            whisper_timeout_seconds=self.whisper_timeout_seconds,
        )
        self._poller.start()

    def stop(self) -> None:
        if self._poller is None:
            return
        self._poller.stop()
        self._poller = None


class FeishuControlChannel:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        chat_id: str,
        on_error: ErrorHandler | None = None,
        poll_interval_seconds: int = 2,
        plain_text_kind: str = "inject",
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.on_error = on_error
        self.poll_interval_seconds = poll_interval_seconds
        self.plain_text_kind = plain_text_kind
        self._poller: FeishuCommandPoller | None = None

    def start(self, on_command: CommandHandler) -> None:
        if self._poller is not None:
            return

        def _forward(command) -> None:
            on_command(ControlCommand(kind=command.kind, text=command.text, source="feishu"))

        self._poller = FeishuCommandPoller(
            app_id=self.app_id,
            app_secret=self.app_secret,
            chat_id=self.chat_id,
            on_command=_forward,
            on_error=self.on_error,
            poll_interval_seconds=self.poll_interval_seconds,
            plain_text_kind=self.plain_text_kind,
        )
        self._poller.start()

    def stop(self) -> None:
        if self._poller is None:
            return
        self._poller.stop()
        self._poller = None


class LocalBusControlChannel:
    def __init__(
        self,
        *,
        path: str,
        source: str | None = None,
        on_error: ErrorHandler | None = None,
        poll_interval_seconds: int = 1,
    ) -> None:
        self.path = str(Path(path))
        self.source = source
        self.on_error = on_error
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._bus = JsonlCommandBus(self.path)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_command: CommandHandler) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(on_command,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self, on_command: CommandHandler) -> None:
        while not self._stop_event.is_set():
            for item in self._bus.read_new():
                try:
                    on_command(
                        ControlCommand(
                            kind=item.kind,
                            text=item.text,
                            source=self.source or item.source,
                        )
                    )
                except Exception as exc:
                    if self.on_error is not None:
                        self.on_error(f"bus control command handler error: {exc}")
            time.sleep(self.poll_interval_seconds)
