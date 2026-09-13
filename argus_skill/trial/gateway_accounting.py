"""Bounded ownership of SQLite work that must outlive a cancelled HTTP request."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from .gateway_observation_queue import GatewayObservations
from .store import Store, TrialError

log = logging.getLogger(__name__)


class RequestMonitor:
    """Observe real disconnects and cancellation while owned work continues."""

    def __init__(self, request, accounting):
        if accounting.closing:
            raise asyncio.CancelledError
        self.request = request
        self.accounting = accounting
        self.task = asyncio.current_task()
        self.initial_cancelling = self.task.cancelling() if self.task else 0
        self.interrupted = False
        accounting._requests.add(self)
        accounting._idle.clear()

    def done(self):
        self.accounting._requests.discard(self)
        self.accounting._signal_idle()

    def check_cancelled(self):
        if self.accounting.closing or (
            self.task is not None and self.task.cancelling() > self.initial_cancelling
        ):
            self.interrupted = True
            raise asyncio.CancelledError

    async def check(self):
        self.check_cancelled()
        if await self.request.is_disconnected():
            self.interrupted = True
            raise TrialError(499, "client_disconnected", "Client disconnected before model dispatch.")
        # Request.is_disconnected uses an AnyIO cancellation scope; retain an
        # asyncio cancellation even when that read consumes its exception.
        self.check_cancelled()

    async def wait(self, future):
        try:
            while not future.done():
                await self.check()
                await asyncio.wait((future,), timeout=0.05)
            await self.check()
            return future.result()
        except asyncio.CancelledError:
            self.interrupted = True
            raise


async def _finish_owned(future):
    """Shutdown cancellation must not abandon a worker or its returned ID."""
    while True:
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                raise


class GatewayAccounting:
    """One bounded lease per request, retained through its final SQLite write.

    Releasing an HTTP slot does not release this capacity. Repeated cancelled
    requests cannot create an unbounded executor queue while SQLite is locked.
    """

    def __init__(self, store: Store, capacity: int):
        self.store = store
        self.capacity = capacity
        self.closing = False
        self._slots = asyncio.Semaphore(capacity)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="argus-billing")
        self._leases: set[AccountingLease] = set()
        self._requests: set[RequestMonitor] = set()
        self._waiters: set[asyncio.Task] = set()
        self._response_closes: set[asyncio.Task] = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self.failures: deque[dict] = deque(maxlen=capacity)
        self.failure_count = 0
        self._close_task = None
        self._close_owner = None
        self.observations = GatewayObservations(self)

    @property
    def pending(self) -> int:
        return len(self._leases)

    def run(self, function, *args, **kwargs):
        future = asyncio.get_running_loop().run_in_executor(self._executor, partial(function, *args, **kwargs))
        # The lease still consumes the result; this also handles a caller that
        # exits before a failed worker has delivered its exception.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return future

    async def acquire(self, monitor: RequestMonitor):
        if self.closing:
            raise asyncio.CancelledError
        if self.failure_count:
            raise TrialError(503, "billing_unavailable", "Trial billing requires durable-state recovery.")
        waiter = asyncio.create_task(self._slots.acquire())
        self._waiters.add(waiter)
        waiter.add_done_callback(self._waiters.discard)
        try:
            await monitor.wait(waiter)
            if self.failure_count:
                raise TrialError(503, "billing_unavailable", "Trial billing requires durable-state recovery.")
        except BaseException:
            # A completed acquisition may lose the race to cancellation of its
            # caller. Return that permit; a cancelled pending acquire restores
            # its own semaphore state.
            def release_late(done):
                if not done.cancelled() and done.exception() is None and done.result():
                    self._slots.release()

            waiter.add_done_callback(release_late)
            waiter.cancel()
            raise
        lease = AccountingLease(self, monitor)
        self._leases.add(lease)
        self._idle.clear()
        return lease

    def release(self, lease):
        self._leases.remove(lease)
        self._slots.release()
        self._signal_idle()

    def _signal_idle(self):
        if not self._requests and not self._leases and not self._response_closes and self.observations.idle.is_set():
            self._idle.set()

    def own_response_close(self, future):
        self._response_closes.add(future)
        self._idle.clear()

        def closed(done):
            self._response_closes.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log.error("Could not close gateway upstream response", exc_info=done.exception())
            self._signal_idle()

        future.add_done_callback(closed)

    async def wait_idle(self):
        await self._idle.wait()

    def failed(self, lease, error):
        self.failure_count += 1
        self.failures.append({"operation_key": lease.operation_key, "request_id": lease.request_id,
                              "error_type": type(error).__name__, "recovery_required": True})
        log.error("Gateway billing cleanup failed; durable reservation requires recovery", exc_info=error)

    async def close(self):
        if self._close_task is None:
            self.closing = True
            self._close_owner = asyncio.current_task()
            self._close_task = asyncio.create_task(self._drain())
        await _finish_owned(self._close_task)

    async def _drain(self):
        for waiter in tuple(self._waiters):
            waiter.cancel()
        if self._waiters:
            await asyncio.gather(*tuple(self._waiters), return_exceptions=True)
        # Requests are normally already stopped by the ASGI server. This also
        # covers explicit lifespan closure while an admission is still waiting.
        owners = set()
        for monitor in tuple(self._requests):
            owner = monitor.task
            if owner is not None and owner is not self._close_owner:
                owner.cancel()
                owners.add(owner)
        # A transport may consume cancellation before returning its response.
        # Keep the client and process lock alive until the request has handed
        # every late response and final accounting intent back to its lease.
        if owners:
            await asyncio.gather(*owners, return_exceptions=True)
        while self._leases:
            await asyncio.gather(*(lease.finish() for lease in tuple(self._leases)))
        if self._response_closes:
            await asyncio.gather(*tuple(self._response_closes), return_exceptions=True)
        await self.observations.close()
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=False)
        if self.failure_count:
            raise RuntimeError("Gateway billing requires durable-state recovery")


class AccountingLease:
    def __init__(self, accounting: GatewayAccounting, monitor: RequestMonitor):
        self.accounting = accounting
        self.monitor = monitor
        self.operation_key = uuid.uuid4().hex
        self.request_id: int | None = None
        self.actual: int | None = 0
        self.inflight = None
        self.reserving = False
        self.cleanup = None
        self.error: Exception | None = None
        self.response = None
        self.response_close = None

    def _can_start(self):
        self.monitor.check_cancelled()
        if self.cleanup is not None:
            raise asyncio.CancelledError
        if self.inflight is not None and not self.inflight.done():
            raise RuntimeError("A billing lease already owns an active operation")

    def reserve(self, key_id: str, amount: int):
        self._can_start()
        self.reserving = True
        self.inflight = self.accounting.run(
            self.accounting.store.reserve, key_id, amount, operation_key=self.operation_key,
        )
        return self.inflight

    def submit(self):
        self._can_start()
        if self.request_id is None:
            raise asyncio.CancelledError
        self.reserving = False
        self.inflight = self.accounting.run(self.accounting.store.submit_operation, self.operation_key)
        return self.inflight

    def finish(self):
        if self.response is not None and self.response_close is None:
            self.response_close = asyncio.create_task(self.response.aclose())
            self.accounting.own_response_close(self.response_close)
        if self.cleanup is None:
            # Publish one immutable intent before any await. Generator cleanup,
            # response cleanup and shutdown cannot race actual usage with None.
            actual = self.actual
            self.cleanup = asyncio.create_task(self._settle(actual))
        return self.cleanup

    async def wait_settled(self, monitor: RequestMonitor | None = None):
        future = self.finish()
        if monitor is None:
            await asyncio.shield(future)
        else:
            await monitor.wait(future)
        if self.error is not None:
            raise TrialError(503, "billing_unavailable", "Trial billing could not be finalized; reservation retained.")

    async def _settle(self, actual: int | None):
        try:
            if self.inflight is None:
                return
            try:
                result = await _finish_owned(self.inflight)
                if self.reserving:
                    self.request_id = result
            except TrialError:
                if self.request_id is None:
                    return  # Admission refused before any reservation commit.
            except Exception:
                # The operation key also finds a commit whose caller never
                # received its ID. A missing operation has no charge to undo.
                pass
            await _finish_owned(self.accounting.run(
                self.accounting.store.settle_operation, self.operation_key, actual,
            ))
        except Exception as error:
            self.error = error
            self.accounting.failed(self, error)
        finally:
            if self.response_close is not None:
                try:
                    await _finish_owned(self.response_close)
                except Exception:
                    pass  # Already reported by the response-close registry.
            self.accounting.release(self)
