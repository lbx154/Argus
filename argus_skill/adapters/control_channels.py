"""Control channels: where commands come IN to the daemon.

Provenance: shaped after ``ArgusBot/codex_autoloop/adapters/control_channels.py``,
trimmed to two adapters:

  * ``TelegramControlChannel`` — wraps the slim Telegram poller from
    ``argus_skill.telegram.poller``.
  * ``LocalBusControlChannel`` — JSONL bus on disk, useful for tests
    and for local CLI control (``argus-skill daemon-inject "text"``
    appends to the same bus).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from ..core.ports import CommandHandler, ControlCommand
from ..daemon.bus import JsonlCommandBus
from ..telegram.poller import TelegramCommandPoller

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
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.on_error = on_error
        self.poll_interval_seconds = poll_interval_seconds
        self.long_poll_timeout_seconds = long_poll_timeout_seconds
        self.plain_text_as_inject = plain_text_as_inject
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
        )
        self._poller.start()

    def stop(self) -> None:
        if self._poller is None:
            return
        self._poller.stop()
        self._poller = None


class LocalBusControlChannel:
    """Read commands from a JSONL inbox file. The CLI writes commands
    into the same file via ``argus-skill daemon-inject``.
    """

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
                            source=self.source or item.source or "bus",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    if self.on_error is not None:
                        self.on_error(f"bus control command handler error: {exc}")
            self._stop_event.wait(self.poll_interval_seconds)


class CompositeControlChannel:
    """Runs several control channels in parallel. Useful when you want
    Telegram + a local file bus active at the same time.
    """

    def __init__(self, channels) -> None:
        self._channels = list(channels)

    def start(self, on_command: CommandHandler) -> None:
        for channel in self._channels:
            channel.start(on_command)

    def stop(self) -> None:
        for channel in reversed(self._channels):
            try:
                channel.stop()
            except Exception:  # noqa: BLE001
                pass
