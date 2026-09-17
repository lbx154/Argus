# Audited historical session linkage — bounded draft contract

Refs #129. This draft stacks on the cold-CLI prevention draft. It implements
**identity-only repair and conservative accounting status**, not complete
historical billing recovery. Both scopes are partial; neither closes #129.

## Supported operator API

Authenticated `POST /api/projects/{sid}/cost-control/session-repair` follows the
existing cost-control decision API's authentication and project resolution.
It accepts only:

- `call_id`: original Argus call identity;
- `session_id`: canonical provider session UUID, used only as a locator;
- `dry_run`: defaults to true;
- `expected_row_hash`, `expected_evidence_hash`: required when applying;
- `reason`: required, bounded operator reason when applying.

Preview returns the reviewed row/evidence hashes, count of exact matched events,
and unresolved-state explanation. Apply the same pair of hashes with
`dry_run=false` and a reason. A stale row or changed original evidence requires
another preview. The request cannot upload events, specify file paths, set
prices/tokens, certify completeness, or acknowledge liability. Failure responses
do not disclose local evidence contents or paths. No unpause/dispatch is done.

## Evidence and supported boundaries

The server reads only its configured project storage and current provider-home
original files: usage row, host call boundaries, raw call stdout, and provider
session events. A nominated UUID is **not evidence**. The provider's original
`session.start` must identify that UUID. Canonical-UUID-ID-bearing original call events must
match the provider event payloads exactly, including at least one
`model.call_finished`. The session's model-request event IDs must match the
original call's set. Timestamp proximity alone cannot establish identity.
Duplicate IDs, contradictory terminal identity, ambiguous IDs in another
session, missing/truncated/oversized evidence, and symlink evidence are rejected.

V1 is deliberately narrow: a failed, non-resumed cold call with null thread ID,
no existing model-usage ownership, no conflicting identity, and no other owner
in the configured projects' usage rows, nested model receipts, or binding
journals. Resumed/multi-owner/delegated sessions are rejected, not assigned a
heuristic time window. Scan bounds: 64 MiB per original file and 2,000 provider
session directories. Rotated-away evidence, other historical provider homes,
and larger stores require offline review; the API has no path override.

Evidence trust is the existing local host/provider-store trust boundary, not a
cryptographic signature. This endpoint does not accept operator-supplied event
objects as authority and does not protect a host whose original files have
already been maliciously rewritten. It is not a provider attestation service.

## Atomicity and audit

An account/project-scope lock serializes repair with new dispatch bindings;
`usage.lock` protects per-record compare-and-swap. One fsynced, atomically replaced
journal snapshot contains both the append-only decision and the exact original
raw usage row, verified event IDs and evidence hashes. There is no separate
binding write that can succeed without its audit. The raw usage row remains
unchanged; normal readers expose the identity through an overlay. Repeating
an already committed decision with its original hashes is idempotent. A failure
after rename may have committed the whole decision; retry with the original
hashes resolves that uncertainty without duplicate decisions. A later change to
the original row is rejected. Repaired sessions are quarantined from new resume
dispatches until an explicit future settlement contract exists.

## Important limitation: linkage is NOT reconciliation or settlement

This version returns `billing_reconciled=false` for historical repairs. It does
**not** feed repaired sessions to today's time-window receipt aggregator:
original event IDs prove identity, but the current SQLite reader does not
expose a sufficiently strong event-to-billing-row mapping or authoritative
cancelled-tail completeness receipt. Reusing it would pretend more than the
verified evidence establishes. Implementing that mapping, safe resumed-session
windows and a provider-backed completeness contract remains outstanding.

The ordinary ledger reader projects the verified thread and
`accounting_pending=cancelled_tail_unverified`, preserving unknown cost and
strict admission within its existing policy/accounting scope (including the
existing day boundary; this draft does not change that scope). Late rows or AIU updates do not synthesize a full bill, zero
charge, completion or admission release. This draft therefore cannot unblock
the reported campaign by itself, even if its original evidence passes preview.

For ordinary failed Copilot calls (not repaired rows), normal known-session
reconciliation continues to collect observed receipts, but pricing remains
`partial` and `accounting_pending` stays set. Known observed cost remains a
lower bound. Verified pre-provider refusal / denied / hosted-trial nonbilling
classifications are preserved. No automatic cancelled-tail settlement is
provided, even if observed rows stop changing.

Cost-control status adds `accounting_state=accounting_pending` while unresolved
calls exist, even when separately acknowledged or permitted by policy. Otherwise
it is `clear` (which does not mean within budget). `blocking_unresolved_calls`
continues to describe admission; accepting liability does not settle accounting.
Existing `paused_cost` / `cost_unreconciled` compatibility labels and real monetary
caps remain; an accounting stop is no longer overwritten as `paused_budget`.
Explicit liability acknowledgement remains separate and unchanged. No repair
invokes it automatically.

## Review and validation

Synthetic fixtures only; no production ledger, event/session IDs, prices, logs,
or campaign artifacts belong in tests. Author adversarial review is not an
independent accounting mutation review: independent review remains outstanding
before this draft is ready. No runtime changes or CI inspection are part of
this publication. See the PR body for exact local test results and limitations.
