# Web request cancellation

The composer stops its current Manager request on the server. Every submission
has a fresh `request_id`; `POST /api/projects/{sid}/message/cancel` targets that
identity. The cancellation POST has its own connection lifetime and a five-second
client deadline. Closing the SSE reader alone does not communicate cancellation.
WebAPI protocol 1.16 advertises `manager.request-cancel.v1`; the matching frontend
requires it, so a new Stop button cannot silently target an older server that
only understands browser disconnection. Update the tenant backends and portal
frontend together during release.

The trial portal reserves connections for cancellation and daemon Stop, separate
from model-backed messages, snapshot reads and objective changes. Cancellation
in the WebAPI is memory-only and does not wait for the Manager session lock or a
shared HTTP worker. The request callback reaches the run gateway and the provider
process through `RunnerOptions.external_interrupt_reason_provider`.

The app owns a bounded request registry. Cancellation arriving before submission
leaves a short-lived marker; a late cancellation cannot stop another request.
Active requests are never evicted. Finished identities and early cancellations
remain for 120 seconds; when retained capacity is full, admission fails explicitly.
IDs are single-use and clients must not replay a failed message POST automatically.
The registry is process-local, matching the current single-process WebAPI runtime.

The worker owns completion of its cancellation lease. If an HTTP coroutine exits
while its synchronous provider thread is still running, that thread remains
cancellable. A cancelled request still waiting for a worker slot cannot enter the
Manager later. Streaming and ordinary message delivery use the same startup
checks: only pending work can restart an executor, and a newer Stop or objective
change invalidates an older handoff. Startup and its acknowledgement share the
daemon command execution lock; model calls and slow status reads stay outside it.

Stopping a reply cancels the foreground request. Work already handed to the team
has its separate task/daemon Stop controls. Changing the selected project only
detaches its browser conversation; it does not implicitly cancel project work.

## Dispatch confirmation

HTTP message routes defer the bridge's queue echo until startup finishes. A
streaming phase reports that the task is saved while startup is pending; then
one confirmation is saved to the transcript and sent through SSE or the shared
Activity channel. Direct bridge callers keep their existing queue echo.
`manager_dispatch_receipt.py` owns this policy independently of pending questions.

Queue insertion says "queued", and a Backlog claim says "claimed". Neither
daemon readiness nor a claim proves that an Engineer/model has started. Fresh
startup failure, admission delay or busy control takes precedence over the
bridge's earlier queue observation. Replaying completed or paused work retains
its duplicate confirmation even while an unrelated control command owns the lock.
The original operator message selects the confirmation language, including when
the Manager rewrites the task title or saves a continuous objective without a task.

The route rechecks cancellation after startup and after preparing the receipt.
Receipt persistence checks again after opening the transcript and before live
publication. An append already completed remains a historical queue receipt;
cancellation neither removes it nor rolls back an already committed task. These
cooperative checks do not promise an atomic transaction spanning filesystem I/O
and the in-memory cancellation registry.

## Verification

`tests/webapi/test_message_requests.py` checks identity, capacity and ordering.
`test_message_cancellation_lifecycle.py` cancels actual route coroutines while
threads are running or queued. `test_message_stop_delivery.py` covers provider
interruption, stale handoffs, resolved questions and paused/completed task replay
through both HTTP message transports. Trial proxy tests hold ordinary connections
open while forwarding cancellation on the reserved pool. Frontend tests cover
independent cancellation, its deadline and request IDs on plain HTTP.

`test_dispatch_receipt_truth.py` drives the real Manager/canonical Backlog through
both HTTP transports with deterministic classification and startup, including
failed startup, admission delay, claims and control contention.
`test_dispatch_receipt_races.py` holds transcript opening or unrelated control
locks to check cancellation and duplicate-message handling.

An isolated ordinary-invitation browser test exercised the built UI, trial portal,
WebAPI and actual bundled Pi process against a deliberately delayed local HTTP
provider. On that run, cancellation returned in 89 ms, Pi returned an external
interrupt in 262 ms, and a new message reached handling in 351 ms. No page errors
or unintended executor starts occurred. These are local acceptance measurements,
not a production latency guarantee; no external model was called in that test.
