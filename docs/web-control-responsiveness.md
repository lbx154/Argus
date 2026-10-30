# Web work controls and request lifetimes

## Workbench navigation and runtime controls

The main views are Map, Conversation, and Workbench. Workbench modules are
Research timeline, Work progress, Code workspace (read-only), and Runtime controls,
in that order.
Runtime controls replaces the separate operations modal and project overview.
Legacy `view=mission` links open the conversation; removed or invalid module
selections open Runtime controls. Task history and execution capabilities are
unchanged. New work and adjustments belong in the conversation rather than a
second composer inside runtime controls.

Runtime controls reads device telemetry while visible and keeps maintenance,
recovery, and diagnostics collapsed until needed. Opening the page does not
check/apply source updates, restart a daemon, or submit work. Stop interrupts
current work immediately. Maintenance commands retain command revisions and
explicit confirmation. Read-only views cannot execute controls.

Each GPU card displays the host's original device index, reported model,
compute utilization, memory usage, and Argus reservation state. Computing,
reserved, memory occupied, and no activity are distinct observations. Host
telemetry includes other programs; zero utilization is not proof that a device
is exclusively available. Probe failures remain visible rather than becoming
idle cards. The authenticated resource API exposes these normalized facts
without device UUIDs, owner identities, or private project paths.

The attachment button uses the existing Argus mark while retaining its Add files
label and file-picker behavior. Core transitions use brief opacity/transform
feedback, do not replay on every streamed fragment, and honor reduced motion.

The TypeScript cockpit has one Stop control for the selected project. After
confirmation, `POST /api/projects/{sid}/daemon/stop` interrupts every call under
way, including the Manager's reply. Files, records and interrupted messages
are kept; Resume (`POST /api/projects/{sid}/daemon/start`) continues the work.
The server exposes `daemon.resumable` when there is work to take up.

A send still waiting for its attachments is cancelled locally because its
message has not reached the server; its text and attachments remain in the
composer. Changing projects only detaches the browser conversation and leaves
server work running. Closing the SSE reader alone does not stop that work.

Keyboard help lists the available shortcuts. Read-only presentation links use
`?kiosk=1`.

## Legacy Python runtime

The following sections describe request lifetimes in the Python runtime, which
continues serving existing projects separately from the TypeScript core.

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

Role prompt reads have their own lifetime. Their ownership rules are documented
below so cancellation changes can be reviewed without treating the whole runner
as a single black box. The hosted trial gateway's accounting, once described
here, was removed from this branch with the old trial code (2026-10-30): hosting
code belongs in the hosting repository (the `hosting` submodule,
lbx154/argus-hosting).

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
through both HTTP message transports.

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
