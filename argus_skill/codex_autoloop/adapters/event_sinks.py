from __future__ import annotations

from pathlib import Path
import sys
from typing import Iterable

from ..core.ports import EventSink
from ..dashboard import DashboardStore
from ..feishu_adapter import FeishuNotifier
from ..live_updates import (
    TelegramStreamReporter,
    TelegramStreamReporterConfig,
    extract_agent_message,
    extract_stream_report_message,
)
from ..telegram_notifier import TelegramNotifier


class CompositeEventSink:
    def __init__(self, sinks: Iterable[EventSink]) -> None:
        self._sinks = [sink for sink in sinks]

    def handle_event(self, event: dict[str, object]) -> None:
        for sink in self._sinks:
            sink.handle_event(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        for sink in self._sinks:
            sink.handle_stream_line(stream, line)

    def close(self) -> None:
        for sink in reversed(self._sinks):
            sink.close()


class TerminalEventSink:
    def __init__(self, *, live_terminal: bool, verbose_events: bool) -> None:
        self.live_terminal = live_terminal
        self.verbose_events = verbose_events

    def handle_event(self, event: dict[str, object]) -> None:
        if str(event.get("type", "")) == "final.report.ready":
            rendered = _render_final_report_message(event)
            if rendered:
                print(f"\n{rendered}\n", file=sys.stdout)
        return

    def handle_stream_line(self, stream: str, line: str) -> None:
        extracted = extract_agent_message(stream, line)
        if extracted is not None and self.live_terminal:
            actor, message = extracted
            print(f"\n[{actor} agent]\n{message}\n", file=sys.stderr)
        if not self.verbose_events:
            return
        prefix = f"[{stream}]"
        to_stderr = stream == "stderr" or stream.endswith(".stderr")
        print(f"{prefix} {line}", file=sys.stderr if to_stderr else sys.stdout)

    def close(self) -> None:
        return


class DashboardEventSink:
    def __init__(self, store: DashboardStore) -> None:
        self.store = store

    def handle_event(self, event: dict[str, object]) -> None:
        self.store.apply_loop_event(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        self.store.add_stream_line(stream=stream, line=line)

    def close(self) -> None:
        return


class TelegramEventSink:
    def __init__(
        self,
        *,
        notifier: TelegramNotifier,
        live_updates: bool,
        live_interval_seconds: int,
        on_error=None,
    ) -> None:
        self.notifier = notifier
        self._stream_reporter: TelegramStreamReporter | None = None
        self._live_updates_closed = False
        if live_updates:
            self._stream_reporter = TelegramStreamReporter(
                notifier=notifier,
                config=TelegramStreamReporterConfig(interval_seconds=live_interval_seconds),
                on_error=on_error,
            )
            self._stream_reporter.start()

    def handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "final.report.ready":
            self._stop_stream_reporter(flush=False)
            _send_final_report_via_notifier(self.notifier, event)
        elif event_type == "pptx.report.ready":
            _send_pptx_report_via_notifier(self.notifier, event)
        elif event_type == "loop.completed":
            self._stop_stream_reporter(flush=False)
        self.notifier.notify_event(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        if self._stream_reporter is None or self._live_updates_closed:
            return
        extracted = extract_stream_report_message(stream, line)
        if extracted is None:
            return
        if extracted.replace_pending:
            self._stream_reporter.replace_message(actor=extracted.actor, message=extracted.message)
        else:
            self._stream_reporter.add_message(actor=extracted.actor, message=extracted.message)

    def close(self) -> None:
        self._stop_stream_reporter(flush=True)
        self.notifier.close()

    def _stop_stream_reporter(self, *, flush: bool) -> None:
        if self._stream_reporter is not None:
            self._stream_reporter.stop(flush=flush)
            self._stream_reporter = None
        self._live_updates_closed = True


class FeishuEventSink:
    def __init__(
        self,
        *,
        notifier: FeishuNotifier,
        live_updates: bool,
        live_interval_seconds: int,
        on_error=None,
    ) -> None:
        self.notifier = notifier
        self._stream_reporter: TelegramStreamReporter | None = None
        self._live_updates_closed = False
        if live_updates:
            self._stream_reporter = TelegramStreamReporter(
                notifier=notifier,
                config=TelegramStreamReporterConfig(interval_seconds=live_interval_seconds),
                on_error=on_error,
                channel_name="feishu",
            )
            self._stream_reporter.start()

    def handle_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "final.report.ready":
            self._stop_stream_reporter(flush=False)
            _send_final_report_via_notifier(self.notifier, event)
        elif event_type == "pptx.report.ready":
            _send_pptx_report_via_notifier(self.notifier, event)
        elif event_type == "loop.completed":
            self._stop_stream_reporter(flush=False)
        self.notifier.notify_event(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        if self._stream_reporter is None or self._live_updates_closed:
            return
        extracted = extract_stream_report_message(stream, line)
        if extracted is None:
            return
        if extracted.replace_pending:
            self._stream_reporter.replace_message(actor=extracted.actor, message=extracted.message)
        else:
            self._stream_reporter.add_message(actor=extracted.actor, message=extracted.message)

    def close(self) -> None:
        self._stop_stream_reporter(flush=True)
        self.notifier.close()

    def _stop_stream_reporter(self, *, flush: bool) -> None:
        if self._stream_reporter is not None:
            self._stream_reporter.stop(flush=flush)
            self._stream_reporter = None
        self._live_updates_closed = True


def _send_final_report_via_notifier(notifier, event: dict[str, object]) -> None:
    raw_path = str(event.get("path") or "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    if not path.exists():
        return
    rendered = _render_final_report_message(event)
    if rendered:
        notifier.send_message(rendered)
    notifier.send_local_file(path, caption="ArgusBot final task report")


def _send_pptx_report_via_notifier(notifier, event: dict[str, object]) -> None:
    raw_path = str(event.get("path") or "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    if not path.exists():
        return
    notifier.send_local_file(path, caption="ArgusBot run report (PPTX)")


def _render_final_report_message(event: dict[str, object]) -> str:
    raw_path = str(event.get("path") or "").strip()
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.exists():
        return "[autoloop] final task report ready, but the file is missing."
    try:
        report_text = path.read_text(encoding="utf-8").strip()
    except OSError:
        report_text = ""
    header = "[autoloop] final task report ready\n" f"path={path}"
    if not report_text:
        return header
    return f"{header}\n\n{report_text}"
