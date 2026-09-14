"""Check settled and observed in-flight spend while a provider is running."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ...core.cost_control import CostControlLockBusyError, _local_day_start
from ...provider_integrations.copilot_usage import read_copilot_usage_since

if TYPE_CHECKING:
    from ._exec_context import _ExecContext


class LiveBudgetMonitor:
    """A CLI session includes the model usage of its nested Copilot agents."""

    def __init__(self, ctx: _ExecContext, *, interval_seconds: float = 5.0) -> None:
        self.ctx = ctx
        self.session_id = ctx.resume_thread_id
        self.interval_seconds = interval_seconds
        self.next_check = 0.0
        self.reason = ""

    def observe(self, stream: str, line: str) -> None:
        if stream != "stdout" and not stream.endswith(".stdout"):
            return
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return
        if not isinstance(event, dict):
            return
        # Never interpret a nested tool argument or a child-agent id as the
        # parent session. The CLI emits its identity before model execution.
        if event.get("type") == "session.start":
            data = event.get("data")
            session = data.get("sessionId") if isinstance(data, dict) else None
        elif event.get("type") == "result":
            session = event.get("sessionId")
        else:
            return
        if isinstance(session, str) and session.strip():
            self.session_id = session.strip()
            self.next_check = 0.0

    def check(self) -> str | None:
        if self.reason:
            return self.reason
        now = time.monotonic()
        if now < self.next_check:
            return None
        self.next_check = now + self.interval_seconds
        reservation = self.ctx.cost_reservation
        if reservation is None:
            return None
        try:
            observed = 0.0
            cursor = getattr(self.ctx, "copilot_usage_cursor", None)
            if self.ctx.backend._is_copilot and cursor is not None and self.session_id:
                usage = read_copilot_usage_since(cursor, session_id=self.session_id, timeout=0)
                if usage is not None:
                    today = datetime.fromtimestamp(_local_day_start(time.time()), UTC)
                    for row in usage.rows:
                        if not row.created_at or row.total_nano_aiu is None:
                            continue
                        created = datetime.fromisoformat(row.created_at.replace("Z", "+00:00"))
                        if created >= today:
                            observed += row.total_nano_aiu / 100_000_000_000
            self.reason = reservation.observe_cost(observed)
        except CostControlLockBusyError:
            # A short bounded retry preserves responsive cancellation without
            # aborting useful work because another caller is settling usage.
            self.next_check = now + min(1.0, self.interval_seconds)
        except Exception as exc:  # noqa: BLE001 - budget failure must fail closed
            self.reason = f"cost control unavailable: {type(exc).__name__}: {exc}"
        return self.reason or None


@contextmanager
def monitor_budget(ctx: _ExecContext, cli_options):
    if ctx.cost_reservation is None:
        yield
        return
    runner = ctx.backend._runner
    previous_callback = getattr(runner, "event_callback", None)
    previous_interrupt = cli_options.external_interrupt_reason_provider
    monitor = LiveBudgetMonitor(ctx)

    def callback(stream: str, line: str) -> None:
        monitor.observe(stream, line)
        if previous_callback is not None:
            previous_callback(stream, line)

    def interrupt() -> str | None:
        reason = previous_interrupt() if previous_interrupt is not None else None
        return reason or monitor.check()

    runner.event_callback = callback
    cli_options.external_interrupt_reason_provider = interrupt
    try:
        yield
    finally:
        runner.event_callback = previous_callback
        cli_options.external_interrupt_reason_provider = previous_interrupt
