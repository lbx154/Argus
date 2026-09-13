# Settlement experience recovery

The Supervisor freezes one bounded factual observation before committing a
mission's completion. The completion transaction carries it in a version 2
mission-delivery envelope, so a stop or process exit after the Backlog fsync does
not lose the observation. Intentional aborts produce no observation; incomplete
iterations and unaccepted reviews cannot become successful lessons.

Delivery and learning progress are independent. Once the completion event and
user receipt are durable, `publication_acknowledged` prevents their repetition
while learning is retried. An unavailable or corrupt experience store does not
hold up delivery or unrelated tasks. Each drain attempts at most eight canonical
insertions, with no waiting for the experience lock, embedding request or model
call. Ordinary recall refreshes the disposable index later.

Insertion is idempotent and cannot overwrite a revised, retracted, merged or
retired observation. Evidence references remain references; completion recovery
does not open the referenced artifacts or infer new causal lessons.

Published deliveries awaiting learning have a 128-record / 2 MiB window, measured
by complete envelope bytes. Oldest completion timestamp and delivery ID define
the retirement order. `mission-experience-retention.json` stores a monotonic
watermark and saturating retirement count before old copies are removed. A
replayed envelope below that watermark remains retired. This is recorded as
capacity retirement, never as successful learning. Undelivered completions are
excluded from the learning window and are not discarded.

Version 1 envelopes remain readable, but contain no recoverable learning capsule.
The prior version 1 runtime (`69a2f6f9c`) rejects version 2. Earlier runtimes that
predate delivery recovery may ignore its files; that rejection is not a general
rollback guarantee. Upgrade all shared Backlog writers together and
include every `Backlog.storage_paths` entry in a quiesced state copy, especially
the pending-delivery directory and retirement metadata. An old binary is not a
safe automatic rollback after a new writer has committed version 2 state.

`tests/life/test_mission_experience_recovery.py` covers delivery failure, actual
process exit at commit boundaries, stop, store recovery, revision preservation,
count/byte pressure, watermark-before-delete crashes and old-envelope replay.
