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

## Waiting between failed model calls

Engineer and Reviewer retry holds use the same 200 ms interrupt checks. The
current request scope is checked first, followed by the retrying role's backend
and the Engineer's mission control callback. Shared callbacks are deduplicated;
the first reason is returned immediately because an abort callback may consume
its mailbox entry. No event log or Backlog scan was added to the polling loop.

Previously the Reviewer slept for the entire 15-second retry delay, while the
Engineer checked stops only every ten seconds. A stopped Reviewer hold now
preserves the completed Engineer output and the unavailable review, then ends
without another model call. A hold without cancellation retains its full retry
delay and retries only the failed role. Tests use both a controlled sleep entry
and real short sleeps through the complete SkillLoop path.

## Ownership boundaries

The accounting worker and role prompt reads have separate lifetimes. Their
ownership rules are documented below so cancellation changes can be reviewed
without treating the whole runner as a single black box.

### Gateway accounting ownership

`trial/gateway.py` owns HTTP admission, provider dispatch and response selection.
`trial/gateway_accounting.py` owns bounded billing leases and a single SQLite
worker. A cancelled HTTP request releases its HTTP slot promptly; its billing
lease remains owned until any late reservation, settlement and upstream response
close finish. Repeated cancellation therefore cannot create an unlimited worker
queue. `trial/gateway_observation_queue.py` coalesces optional status snapshots
with a separate bound and never makes HTTP wait for those snapshots.

`trial/store.py` remains the accounting authority. A durable operation key makes
reservation replay idempotent and links cleanup to a reservation even when its
ID never reached the HTTP caller. The provider may be called only after a
`submitted` intent commits. On restart, an active managed `reserved` operation
can be refunded; `submitted` and legacy interrupted work remain conservatively
charged. Submission intent alone does not prove a provider received the call.
Terminal settlement fixes the charge and TPM expiry once.

Each lease freezes its selected usage before asynchronous cleanup starts.
Cancellation or a later fallback cannot replace known usage with an unknown
estimate. Successful JSON/SSE completion waits for settlement within its
deadline. Cancelled, timed out or failed streams detach from cleanup while the
gateway keeps ownership. A durable settlement failure makes health report
`billing_recovery_required` and blocks new model admissions until recovery.

An admitted lease also owns one disconnect watcher during upstream preparation,
response headers and JSON body reads. A real `http.disconnect` cancels the
current request task while it awaits upstream I/O. The watcher stops
synchronously before SSE handoff, where StreamingResponse takes over disconnect
listening. No new await splits response ownership from handoff. Cleanup collects
the watcher before returning billing capacity, including cancellation consumed
by AnyIO or cancellation before the watcher started.

The request still owns a transport that consumes cancellation and returns late:
its response is closed without reading a late body, and shutdown retains the
process lock until cleanup ends. This is cooperative cancellation, not forced
termination of arbitrary transport code. Once valid JSON usage has been read,
it is captured before the final disconnect check so a late disconnect cannot
replace known usage with the reservation estimate. The current authorization
method reads its vault synchronously; a synthetic awaited-authorization barrier
does not prove that vault I/O itself became interruptible.

Lifespan shutdown rejects new request owners and drains requests, leases,
response closes and observations before releasing `gateway.lock`. Tests and
operators use `accounting.wait_idle()` to establish quiescence; observing zero
active rows before a blocked reservation commits is insufficient. The worker's
SQLite transaction timeout still applies: cancelling HTTP does not kill a
transaction already running in its worker.

### Role prompt reads

Each ordinary `SkillLoop.execute` receives a retained interrupt provider with
the mission identity and Manager root fixed at entry. Prompt preparation and
the backend share its first observed reason, including one-shot mission aborts.
The provider expires when that execution exits. Backend callback translation
must preserve this context for watchdog threads, including threads that do not
inherit Python context variables; an old callback must never consume the next
mission's mailbox.

Engineer/Reviewer required OperatorContext projection reads and optional
experience recall use cancellation-aware lock acquisition only around prompt preparation. Once
acquired, one-time instruction consumption remains atomic. Required context
failures retain their typed error unless an actual interrupt is available to
the gateway. Settlement does not inherit a cancelled file-lock budget.

A knowledge-cache read under a prompt lock budget uses a short SQLite busy wait.
BUSY/LOCKED means another writer owns the database, not that the cache is
corrupt: recall can fall back to current canonical Markdown without deleting or
rebuilding that writer's cache. These boundaries do not make every filesystem
operation or non-cooperating custom backend interruptible.

Nonempty transient operator-message intake is a separate durability boundary.
Its inbox cursor has already advanced before classification and directive
persistence. Cancelling that append could lose the operator's instruction, so
this path retains its existing complete write boundary and up to 30-second
lock wait. Prompt-read timing acceptance excludes this boundary. Removing that
remaining Stop delay requires an acknowledged inbox claim or durable replay
protocol before making intake cancellation-aware.

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
