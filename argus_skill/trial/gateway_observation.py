"""One gateway attempt record; no prompts, training events or admission policy."""
from __future__ import annotations

import logging
import sqlite3
import time

from starlette.responses import StreamingResponse

from .store import Store

log = logging.getLogger(__name__)


class GatewayStreamingResponse(StreamingResponse):
    """Run gateway cleanup even when ASGI send fails before streaming starts."""

    async def __call__(self, scope, receive, send):
        cleanup, self.background = self.background, None
        try:
            await super().__call__(scope, receive, send)
        finally:
            if cleanup is not None:
                await cleanup()


class GatewayAttempt:
    """Observe backend outcomes, not delivery of HTTP headers or SSE to a client.

    A completed stream means a valid upstream terminal and usage were observed.
    selected_response_status is the chosen HTTP status, not a delivery receipt.
    Expected storage failures must not change routing, accounting or cleanup.
    """

    def __init__(self, store: Store, key_id: str, estimated_tokens: int, *, dispatch=None):
        self.store = store
        self.key_id, self.estimated_tokens = key_id, estimated_tokens
        self.started_at = store.clock()
        self.dispatch = dispatch
        self.attempt_id = None
        self.finished = False
        self.fields = {"phase": "slot", "slot_wait_ms": 0, "tpm_wait_ms": 0}
        self.slot_start = time.monotonic()
        self.tpm_start = None
        if dispatch is not None:
            dispatch(self)
            return
        try:
            self.attempt_id = store.begin_gateway_attempt(key_id, estimated_tokens)
        except sqlite3.Error:
            log.exception("Could not start gateway attempt observation")

    def _write(self, **fields):
        self.fields.update(fields)
        if self.dispatch is not None:
            self.dispatch(self)
            return
        if self.attempt_id is not None:
            try:
                self.store.update_gateway_attempt(self.attempt_id, **self.fields)
            except sqlite3.Error:
                log.exception("Could not update gateway attempt observation")

    def waiting_for_slot(self):
        if not self.fields.get("queue_reason"):
            self._write(queue_reason="slot")

    def acquired_slot(self):
        self._write(phase="admission", slot_acquired_at=self.store.clock(),
                    slot_wait_ms=(time.monotonic() - self.slot_start) * 1000)

    def waiting_for_tpm(self):
        if self.tpm_start is None:
            self.tpm_start = time.monotonic()
            self._write(phase="tpm", queue_reason="slot,tpm" if self.fields.get("queue_reason") else "tpm")

    def admitted(self, reservation_id: int):
        self._write(phase="authorization", admitted_at=self.store.clock(), reservation_id=reservation_id,
                    tpm_wait_ms=(time.monotonic() - self.tpm_start) * 1000 if self.tpm_start is not None else 0)

    def upstream(self):
        self._write(phase="upstream")

    def upstream_response(self, status: int):
        self._write(upstream_status=status)

    def streaming(self):
        self._write(phase="stream", selected_response_status=200)

    def finish(self, outcome: str, *, selected_status=None, error_code=None, retry_after=None):
        if self.finished:
            return
        self.finished = True
        timing = {}
        if "slot_acquired_at" not in self.fields:
            timing["slot_wait_ms"] = (time.monotonic() - self.slot_start) * 1000
        if self.tpm_start is not None and "admitted_at" not in self.fields:
            timing["tpm_wait_ms"] = (time.monotonic() - self.tpm_start) * 1000
        self._write(outcome=outcome, finished_at=self.store.clock(), client_error_code=error_code,
                    retry_after=retry_after,
                    **({"selected_response_status": selected_status} if selected_status is not None else {}), **timing)
