from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .daemon_bus import JsonlCommandBus


@dataclass
class LocalControlCommand:
    kind: str
    text: str


CommandCallback = Callable[[LocalControlCommand], None]
ErrorCallback = Callable[[str], None]


class LocalControlPoller:
    def __init__(
        self,
        *,
        control_file: str,
        on_command: CommandCallback,
        on_error: ErrorCallback | None = None,
        poll_interval_seconds: int = 1,
    ) -> None:
        self.control_file = str(Path(control_file))
        self.on_command = on_command
        self.on_error = on_error
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._bus = JsonlCommandBus(self.control_file)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

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
            for item in self._bus.read_new():
                try:
                    self.on_command(LocalControlCommand(kind=item.kind, text=item.text))
                except Exception as exc:
                    if self.on_error is not None:
                        self.on_error(f"local control command handler error: {exc}")
            time.sleep(self.poll_interval_seconds)
