# Accounting integrity review unit B

This change is a prevention/debt-preservation release, not a historical billing
repair or a campaign acceptance certificate. It is based on the v0.1.7 accounting
layout and does not change provider selection, monetary limits or the unpriced
cost policy.

## Enforcement

* `AccountingIntegrityError` identifies the file, physical byte offset, length
  and digest without exposing its raw payload. Invalid JSON, non-finite numbers,
  non-object usage rows, missing call identities and unterminated tails fail
  before append, admission or reconciliation. Damage outside the daily window
  still blocks. A broken project cannot disappear from global enforcement via
  an exception-swallowing reader.
* `UsageLedger.records()` and `summary()` are read-only, including when the
  legacy constructor option is used. Call `reconcile()` explicitly to migrate
  or reprice clean evidence. Reconciliation validates again under the append
  lock; it does not salvage malformed lines. Cost snapshots project state but
  never persist pruning, migration or reconciliation.
* Cost-state writes require file fsync, atomic replace and POSIX directory
  fsync. An fsync/rename error is a failure, including an error after rename
  became visible. Do not report successful persistence merely because a file
  can be read back. Directory durability on Windows is not proved here.
* Admission-lock contention is denial, not permission to run without a durable
  reservation. State version **3** preserves v1/v2 liabilities and reservations
  over midnight. Older writers reject v3. Do not mix old/new active writers.
  Outstanding observed lower bounds remain conservatively counted until
  resolved; midnight no longer silently removes them.

## Finalization obligations

`cost-finalizers/<hash-of-reservation-id>.json` records a predispatch obligation,
original reservation identity, receipt candidates and errors. A separate OS
lease is held for the lifetime of the call/finalizer, not the daemon PID's
lifetime. Clean parallel calls can hold different leases.

The reservation is durably written first; the intent is durably written before
provider execution. A failure in either step prevents dispatch. Receipt intent
is written before usage append. Settlement ensures a matching receipt is in the
canonical usage ledger before closing debt. Conflicting late receipts remain
blocked, not overwritten or double charged. Closing the intent follows durable
cost-state settlement. A finalizer failure releases its lease, retains its
obligation and changes the work result to an accounting failure.

If ENOSPC prevents even recording the latest error/receipt, the previously
persisted obligation plus released lease still blocks admission. This cannot
magically recover bytes never persisted anywhere; the missing tail stays
unknown. No stale PID, zero observation, partial receipt or timeout proves a
call unbilled. A legacy reservation without an active verified intent lease is
also unresolved, even if its daemon PID is alive. Closed intent files are kept;
this release has no intent-GC or automatic recovery mechanism.

Integrity checks run at the common AgentCliBackend admission path independently
of the optional monetary-control toggle, and again inside the pre-execution
project guard. Planner, engineer, reviewer, Manager and external LLM work routed
through that backend use the same fence. Bare low-level provider runners and
custom backends that bypass AgentCliBackend are not covered by this contract.
A caller must bind the correct project root; this release does not discover or
repair incorrectly bound external-job ancestry.

## Execution dependency (separate review A)

Execution pause, provider leases and retained-origin scheduling are owned by
[`../execution/safety-boundary.md`](../execution/safety-boundary.md), not this
patch. The execution guard reads no usage and quiesce takes no cost-state lock.
`_accounting_admission.py` supplies mandatory registration, before-dispatch
integrity checking and exceptional-finalizer retention through A's small
`AccountingAdmission` result. Accounting permission never clears a pause, marks
work successful, requeues a failed attempt or transfers an owner.

`finalization_intents.py` owns the original obligation, receipt validation and
lease-retention protocol formerly mixed into `dispatch_safety.py`. No accounting
implementation is duplicated in A. Cost preflight/reservation still respects
A's execution assertion; project ledger validation now lives explicitly in
accounting, including external-project preflight and under-lock rechecks.
Neutral strict JSON and durable-IO primitives come from A's `safety_io.py`.

## Historical damage and activation boundary

There is no salvage apply endpoint in this release. A complete suffix embedded
behind an ENOSPC-truncated prefix must be corroborated against its canonical
usage event, settlement identity and provider receipts. Cross-stream event
payloads need not be identical. A future authenticated preview/CAS/audit overlay
must preserve every original byte, recover each original receipt at most once,
and retain the truncated prefix and missing provider tails as unknown. An
observed charge is not a completeness certificate or authorization for another
liability. Never use normal reconciliation or manual JSON editing as salvage.

Before activation: independently review the exact source commit and local
fault/incident-copy receipts; retain the existing external containment; inspect
storage and all writer ownership; rehearse an old-owner cutover on private
copies. Do not stop a legacy debt owner while a legacy reader could prune its
reservation. Do not resume continuous planning or autonomous supervision until
integrity, reservations and unique ownership are all recovered. This source
change alone intentionally does not satisfy those campaign-resumption gates.

## Local test contract

The standalone accounting gate exercises real ledger/reservation/finalizer
persistence with synthetic fixtures, all R1–R6 regression contracts and the
reproduced snapshot race. Backend regression probes use fake transport, not
provider calls. The integrated gate additionally runs A's independent execution
suite and all prior selected tests. Private actual-incident copies stay outside
Git and are replayed on disposable writable copies in a no-network namespace.

## Revision-2 integrity contract

Durable call registration is mandatory even when monetary control is disabled.
Only cap/unpriced-policy evaluation and monetary event presentation are optional;
the per-call reservation, finalizer lease, failure gate and terminal persistence
are not. An append or error-journal failure leaves the obligation visible and
prevents a second provider dispatch. Live monetary configuration is not changed.

Usage append fsyncs both the ledger file and project directory before settlement
can close. A closed intent is corroborated on admission against its canonical
project receipt, or an explicit caller-not-started release with zero observed
usage. Closed unknown/partial settlements retain the original observed lower
bound in both the intent and unresolved state until a complete receipt exists.
Missing/conflicting canonical records or missing debt block; admission never
reconstructs them. An active reservation is removed only by its finalizer, not
because a receipt happened to arrive first. Same-call finalization/observation
is serialized, and observation cannot resurrect a closed reservation.

Receipt call ID and canonical project/root identities are checked before intent,
ledger or cost-state mutation. A symlink alias resolves to the same canonical
project; display model/role aliases and resumed session labels are not required
to equal reservation display values. The explicit canonical rewrite writer
stores exact prior receipts in `accounting_history` within the same atomic
replacement. Closed intents accept these retained versions. Immutable call,
project, provider, mission, role, status, source and timing cannot change in this
history; an already bound provider-session ID cannot be replaced. A missing
session may be populated by normal explicit reconciliation. This is local
writer provenance, not a signed billing-completeness or historical-salvage API.
Raw ledger replacement without the required provenance is not reconciliation.

Accounting JSON rejects duplicate object keys at every nesting level. Usage
identities, supported schema versions, status enums, timestamps, monetary fields,
integer token counts and model-receipt conflicts are validated before lossy
projection. Missing `schema_version` is the established unversioned v1 usage
format, not permission to omit call/project/provider identity or timestamps.
Cost-state v1/v2/v3 fields are validated without coercing strings, booleans,
negative or non-finite values into spend. Conflicting duplicate call IDs are
checked across the whole ledger history and all known projects before daily
filtering. Exact identical usage repeats are idempotent; duplicate state or
intent identities are not. A live reservation and its partial unresolved
projection may coexist only with consistent project/provider identity.

Liability discharge uses complete history, independently from daily USD/token
spend. Genuine unknown prior-day debt remains; complete historical receipts do
not disappear from discharge evidence after midnight. Read-only projections
remain non-persistent and do not need a new reservation to keep debt resolved.

Monetary admission captures the cost-state, project-directory and ledger file
revisions before collecting usage, then compares them while holding the cost
lock. A changed snapshot is denied; it is never combined with newer empty
reservation state. The revision tuple uses device/inode/size/mtime/ctime, not
content hashes, on the supported local POSIX filesystem. No usage lock is taken
under the cost lock. Registration and pre-execution lease scans serialize on
the cost lock so a half-created intent is not mistaken for a lost finalizer.
This closes the reproduced settlement/admission race for cooperating writers;
it does not promise zero overshoot for unreported in-flight provider spending,
atomic diagnostic snapshots, remote filesystems, or old writers. Legacy-owner
cutover, historical salvage, ownership recovery and independent exact-commit
review remain prerequisites. No revision-2 pass authorizes activation.

## Separated B review correction: event, projection and lifecycle invariants

The B1–B4 correction remains accounting-only on the original separated A
prerequisite. It does not alter the execution fence, scheduler or admission
contract, and it does not activate a runtime.

- Validate every `(trimmed session_id, usage_event_id)` used by the fold across
  the entire relevant ledger set, including a newly referenced external project,
  before date filtering or deduplication. All projected money/token fields must
  agree; missing evidence is not explicit zero. Identical event evidence remains
  one charge, even with distinct call/project IDs or differing display metadata.
  Explicit canonical reconciliation still retains prior receipt versions; old
  versions are provenance, not additional charge events.
- A nonempty model-detail list replaces aggregate money only when it preserves
  every known monetary/nano-AIU lower bound. A priced list must price every item;
  empty detail, a smaller subtotal, and inconsistent nano-AIU/USD evidence are
  rejected before finalization mutation. Stronger detailed evidence remains a
  conservative bill, not a reason to erase it in favor of a smaller aggregate.
  Nano-AIU converts using the existing provider constant, including an aggregate
  whose USD field is missing (`canonical_aggregate_nano_aiu` provenance).
- Token-only incomplete detail retains an explicit
  `call_aggregate_token_lower_bound` residual contribution, with its call ID.
  This does not introduce a dollar charge. Overlapping *incomplete* token
  telemetry may conservatively overestimate tokens; it never silently drops a
  known call lower bound. Complete identical event tokens still deduplicate.
- `before_dispatch` first checks accounting integrity, then durably marks
  possible execution through B's existing hook. `release()` checks the retained
  and persisted lifecycle before any write: no failure, no possible execution,
  no executed prepared receipt, an active lease and the original reservation.
  Zero observations or a `not_started` reason cannot supply those facts.
  Errors/failure flags are retained across later preparation. A failed marker
  write retains the in-memory fact and original obligation, never permission.
- Genuine pre-transport release has explicit checked-lifecycle evidence in its
  closed intent. Legacy closed releases without that evidence fail closed; this
  change does not silently certify or migrate old releases. Canonical backend
  denial receipts use ordinary `settle(record)` rather than `release()`. With
  possible execution but no project/receipt, the backend preserves an unresolved
  obligation via ordinary unknown settlement; it does not forgive it from an
  error message. Original result/error propagation is preserved.
- Closure verification has one invocation-local validated ledger index per
  resolved project and one indexed unresolved-state view, not one full ledger
  and state reread per intent. It retains all intents and obligations. No cache
  survives a check, and no usage lock is acquired while holding the cost lock.
  The existing monetary admission revision comparison and all lock timeouts are
  unchanged. This removes the demonstrated quadratic clean-history convoy; it
  is not a guarantee of arbitrary-scale or power-loss availability.

Before integrating A's concurrent revision, the parent must test its mandatory
hook/literal-boolean checks against this B adapter (positive and denied results,
policy off, pre-dispatch failure, typed post-entry failure, and local denial).
B already returns literal booleans and supplies the existing success/failure
hooks; this is source-level compatibility reasoning, **not** an integration
receipt for the unmerged A revision. Required independent re-review, original
owner/debt-preserving cutover, historical billing completeness and explicit
activation authorization remain separate blockers. Containment stays untouched.

### B3: no-charge lifecycle and financial projection correction

The possible-execution marker is not a no-spawn proof. Both public
`prepare_finalization(record)` and `settle(record)` validate denial/not-billed
receipts against the retained **and persisted** lifecycle, original reservation,
lease and observations before writing anything. Failed/uncertain lifecycle,
positive money or token observations, and contradictory denial evidence reject.
Backend finalization performs prepare/append/close under the reservation's existing
serialization lock; it no longer appends a denial between separate prepare and
settle operations. `release()` retains its stricter not-entered contract.

A call-local accounting scope spans authorization retries. Producer proof is a
single-use, call-bound in-process capability; a caller-supplied kind/status string,
dictionary or stale capability cannot mint it. This is not a cryptographic defense
against arbitrary in-process code or disk mutation. Known cold Copilot
preparation refusal is recorded at the actual preparation boundary; ENOENT is
certified only around the process constructor. Exceptions from arbitrary runner
entry, streaming, callbacks or cleanup do not supply this proof, including an
ordinary `FileNotFoundError` or a preparation exception thrown after dispatch.
The durable no-charge evidence is separate from (and never clears) the possible
execution marker. Later attempts invalidate it; a refusal after a prior possible
attempt cannot discharge that attempt. Existing matching, silent local-parser
completion receipts remain a distinct checked proof path. These are trusted
internal producer contracts, not new user/provider attestation endpoints.

Closed-intent validation enforces the same no-charge evidence/observation rules,
including an unknown obligation followed by a late denial row. No old failed or
possibly-executed intent is automatically certified by its zero canonical row.
A historical `denied` row containing positive **or unknown** money, token or model
evidence is contradictory and fails strict reading/reconciliation. A denial
label and retained rewrite history are not authority to erase a bill. Explicit
consistent canonical repricing of actual priced calls remains unchanged, with
exact previous versions retained and current money counted once; this local
writer contract is not independent provider-price verification or historical
salvage authorization.

Token residuals and validation now use the same call-local unique event view,
before global contribution deduplication. Repeated identical details cannot
inflate the explained token subtotal and drop the call lower bound. Global money
is still deduplicated once; overlapping incomplete token bounds retain the
previous conservative semantics.

Compatibility is intentionally fail-closed: fixtures or old data expecting a
paid denial to become zero, a denial to erase token observations, or ambiguous
runner exceptions to prove no charge are not grandfathered. The original probes
and receipts remain immutable; corrected synthetic safety expectations are in
`tests/core/test_accounting_revision_b3.py`. B3 still has the original A
prerequisite, not the concurrently revised/accepted A. No installation, live
repair, old-owner cutover, scheduler change or activation is part of B3.

## Current upstream transplant

This branch builds on the execution-safety PR against current `dev`, not on PR130
or PR131. It includes the separately reviewed three-file execution/accounting
binding glue: mandatory real admission hooks, non-accounting failure finalization,
and canonical execution owner separate from accounting storage. Direct-call
project guards remain active.

Current upstream has no PR130 `prepare_session` producer boundary. Consequently
an ordinary preparation exception is conservatively uncertain, just like an
ordinary post-entry typed exception. The actual process-constructor ENOENT and
validated local startup-receipt routes retain their bounded no-charge evidence.
No obsolete session-binding or historical repair behavior is imported to satisfy
a fixture. A synthetic preparation case explicitly tests the stricter refusal,
with no canonical free receipt and retained obligation; no test is skipped.

Tests requiring paid denied evidence to become free now require rejection before
mutation, following the reviewed accounting contract. The ordinary repricing and
no-reuse control remains after resetting only that test's deliberate corruption.
The synthetic admission context includes the real execution-owner field. These
are explicit fixture/expectation dispositions, not an unchanged-old-tests claim.
