from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class ControlCommand:
    kind: str
    text: str = ""
    source: str = "unknown"


CommandHandler = Callable[[ControlCommand], None]


class ControlChannel(Protocol):
    def start(self, on_command: CommandHandler) -> None:
        ...

    def stop(self) -> None:
        ...


class NotificationSink(Protocol):
    def send_message(self, message: str) -> None:
        ...

    def close(self) -> None:
        ...


class EventSink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> None:
        ...

    def handle_stream_line(self, stream: str, line: str) -> None:
        ...

    def close(self) -> None:
        ...
