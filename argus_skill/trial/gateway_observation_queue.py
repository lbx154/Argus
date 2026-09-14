"""Bounded, coalesced optional writes on the gateway's SQLite executor."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

log = logging.getLogger(__name__)


class GatewayObservations:
    def __init__(self, accounting):
        self.accounting = accounting
        self.limit = 2 * accounting.capacity
        self.pending = {}
        self.active = None
        self.task = None
        self.dropped = 0
        self.idle = asyncio.Event()
        self.idle.set()

    def submit(self, attempt):
        if attempt not in self.pending and len(self.pending) >= self.limit:
            self.dropped += 1
            return  # Observation capacity can never consume billing capacity.
        self.pending[attempt] = dict(attempt.fields)
        self.idle.clear()
        self.accounting._idle.clear()
        if self.task is None:
            self.task = asyncio.create_task(self._pump())

    async def _pump(self):
        try:
            while self.pending:
                attempt = next(iter(self.pending))
                fields = self.pending.pop(attempt)
                self.active = attempt
                try:
                    # At most one optional job is queued. Billing and these
                    # fail-fast writes share one worker, preventing internal
                    # SQLite lock races without waiting in the ASGI task.
                    for retry in range(3):
                        try:
                            attempt.attempt_id = await self.accounting.run(self._write, attempt, fields)
                            break
                        except sqlite3.OperationalError as error:
                            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or retry == 2:
                                raise
                            # Closing the last concurrent WAL reader may take
                            # a brief checkpoint lock. Retry outside both the
                            # ASGI task and worker; ongoing external contention
                            # drops after three attempts, without waiting in
                            # SQLite or delaying an HTTP response.
                            await asyncio.sleep(0.005)
                except sqlite3.Error:
                    self.dropped += 1
                    log.exception("Could not persist gateway attempt observation")
                finally:
                    self.active = None
        finally:
            self.task = None
            self.idle.set()
            self.accounting._signal_idle()

    @staticmethod
    def _write(attempt, fields):
        identifier = attempt.attempt_id
        if identifier is None:
            identifier = attempt.store.begin_gateway_attempt(
                attempt.key_id, attempt.estimated_tokens, started_at=attempt.started_at,
            )
        # Retain the ID even if this optional update loses an external writer
        # race. A later snapshot must not create a second attempt record.
        attempt.attempt_id = identifier
        attempt.store.update_gateway_attempt(identifier, **fields)
        return identifier

    async def close(self):
        if self.task is not None:
            await asyncio.shield(self.task)
