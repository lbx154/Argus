# Bounded offline-copy recovery (draft library)

Refs #132. This is the third layer above execution safety (#133) and accounting
integrity (#134). It is **not a live-state repair command**, release, runtime
activation, migration installer, or complete resolution of the issue.

## Why a copy-only API

Current writers do not share a global recovery fence: cost state uses the cost
lock, usage uses its own lock, event append/rotation uses the event lock, and
finalizer intents have per-obligation leases. Legacy writers likewise do not
participate in a new offline-copy lock. A `writers_stopped=True` parameter would
not prove exclusion. This library therefore has no supported arbitrary-root
prepare/apply entry point and no CLI.

`OfflineRecovery.create(parent, originals, provider_sqlite)` takes immutable bytes
only and creates a fresh random mode-0700 child directory. The parent must already
be owned by the caller and private (no group/other permissions). **The caller must
exclusively own the parent namespace for the operation's entire lifetime.** Do
not point any daemon, CLI writer, provider, observer, or other runtime at that
copy. No process is stopped, no global OS permissions are changed, and no live
source path or provider database is opened by the library.

Only library operations are cooperating writers: an OS lock excludes concurrent
reopen, a per-handle lock serializes operations, and resume requires the separately
retained random token plus the recorded root device/inode. These protect against
accidental adoption/concurrency, not a hostile same-user process, namespace
replacement by another owner, or an old runtime launched deliberately into the
copy. No lock here contains an unaware writer. Namespace ownership is an explicit
operating prerequisite, not something a boolean or PID scan can certify.

## Supported evidence and shape

Version 1 deliberately supports one narrow shape:

- Exactly five byte inputs: `cost-control.json`, `cost-control.jsonl`, `config.json`,
  and `projects/<project_id>/{usage,events}.jsonl`.
- Exactly one reviewed malformed usage-line prefix and two reviewed malformed
  event-line segments (`provider.request.completed` and `life.planner.start`).
  Cuts bind offset, length, and SHA-256. Valid records cannot be archived; cuts
  cannot cross a physical line. No heuristic search or skip-invalid behavior.
- One complete canonical known-call receipt, its exact `usage.recorded` event,
  and a sealed standalone SQLite `assistant_usage_events` snapshot corroborating
  receipt IDs, session/model, tokens, timestamps, and nano-AIU charge.
- One legacy reservation with no finalizer intent, bound to the failed call's
  reservation/start/completion events. Migration preserves it as an explicitly
  unknown blocking obligation, not a completed, free, or zero-cost call.

Each claimed provider ID must identify exactly one SQLite row, with an integer
ID and all corroborated fields matching. Tables without a uniqueness constraint
are allowed only when each claimed ID still has exactly one row. Duplicate rows
for a claimed ID are rejected even when identical; contradictory duplicates are
never silently selected or deduplicated. The canonical model receipt list and
spec ID list must agree in order and contain each ID exactly once. Repeated model
receipts are unsupported here, even if a general usage reader can deduplicate
them. Multiple distinct receipt IDs are supported, with their total charged once.
Unclaimed database rows do not establish additional charges or completeness.

The SQLite schema is provider-specific, not a general receipt adapter. Supplying
bytes and matching hashes proves internal consistency, **not provider authenticity
or completeness**. The operator must separately verify trustworthy acquisition
of a sealed database (including any WAL) and approval of the exact spec. This API
never reads an active provider store or authorizes a risk acknowledgement.

`source_project_root` is the original absolute identity string in the reservation;
it is never opened or rebound. Other original identities, debt lower bounds,
accounting day/timestamp, existing acknowledgements and source bytes are retained.
There is no budget reset, pricing-policy change, or new approval.

## Library workflow

1. Independently capture/review the five sealed originals and provider SQLite
   bytes. Snapshot acquisition, stopping legacy writers and live migration are
   deliberately **not implemented** here.
2. Create a private parent and call `OfflineRecovery.create`. Retain the returned
   `root.name` and `resume_token` separately and securely before applying. The
   token is a local capability; do not put it in reports or source control.
3. Build a version-1 spec. Its exact keys are `version`, `project_id`,
   `source_project_root`, `reservation_id`, `known_call_id`, `unknown_session_id`,
   `originals`, `usage_cuts`, `event_cuts`, `provider_evidence`. `originals` maps
   the five relative names to hashes. Cuts have exactly `offset`, `length`,
   `sha256`. Provider evidence has exactly `sqlite_sha256`, `ids`, `nano_aiu`.
   Unknown fields/versions and unsafe relative names reject.
4. `work.prepare(spec)` archives the originals/segments/provider evidence and
   writes validated projections. Review the returned transaction ID and manifest
   hash independently. Synthetic tests show a complete executable spec example.
5. `work.apply_to_copy(id, expected_manifest_sha256=reviewed_hash)` applies only
   inside the generated copy. No-follow descriptor-relative I/O, pinned directory
   identities, pre-lock and under-lock CAS, per-file/directory fsync and a durable
   pending marker protect the transaction. `read_state()` rejects pending state.
6. After interruption, use `OfflineRecovery.resume(parent, name,
   resume_token=retained_token)` and the **same reviewed manifest hash**. Each
   non-projected original must still have its original digest; each projected
   original must have its original or projected digest. Missing or changed
   originals reject before any apply mutation, both initially and while pending.
   Only the explicitly new finalizer destination may be absent (or, while pending,
   already projected). A completed replay
   is a no-op and preserves later acknowledgements; a partial transaction never
   overwrites unexpected changes. A crash during preparation has no published
   apply manifest guarantee: preserve that copy and start a new one from sealed
   bytes rather than deleting/reusing its partial archive.
7. Close the handle; the copy is retained for inspection. Export/review is manual.
   **Do not copy the output over a live root or configure a runtime to use it.**
   A separately designed/reviewed offline cutover is still required.

Underscore-prefixed helpers are internal implementation, not supported live-root
APIs or a Python security sandbox. The library does not install recovery hooks
into the runtime. Pending gating and recovered-unknown validation are exercised
on the offline copy; existing runtime finalization acceptance is unchanged.

## Validation and limits

All new fixtures are generated: two projects, distinct receipt IDs and charges,
SQLite tables, corrupt journal prefixes, original reservation state, and old/new
synthetic acknowledgements. Coverage includes exact canonical charge once,
unknown/lower-bound retention, original-byte preservation, day preservation,
manifest/provider tampering, pre-mutation CAS, symlink ancestry/leaf rebinding,
late replace redirection, every projection boundary, actual process exit/resume,
and cooperating-writer lock exclusion.

Tests run with existing dependencies, network disabled, source/host read-only,
and synthetic writable temporary directories. No live incident fixture, provider
execution, full runtime migration or Windows durability is certified. This is a
POSIX local-filesystem library; no Windows/network-filesystem portability claim.
Independent review of this new public artifact is pending. CI was not queried.
