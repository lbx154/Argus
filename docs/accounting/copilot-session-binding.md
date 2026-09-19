# Copilot cold-CLI session binding (Refs #129, partial prevention)

The accounting-enabled AgentCliBackend now installs a per-call binding callback.
After the ACP routing decision, and before cold subprocess spawn, the runner:

1. Rejects identity selectors in default/per-call extra arguments (`--session-id`,
   `--resume`, `--continue`, including equals forms).
2. Preserves an existing resume identity. For a new session only, probes the
   resolved executable's `--help` for `--session-id`, using the dispatch child
   environment (including Node-wrapper PATH repair) and hidden-process options,
   then allocates a UUID. Rechecks the composed stop/budget gate after the probe
   and before committing any binding or starting the provider.
3. Writes the call/session decision to `usage.provider-sessions.json`, under
   `usage.lock`, using fsync plus atomic replace. Failure prevents dispatch.
4. Supplies `--session-id` for a new call or the existing `--resume` for a resume.

The journal holds identity/audit decisions, **not prices**. The usage ledger is
still the aggregation source. Callback state is per call, not runner-global.
Identity survives cancellation and post-spawn exceptions; failures are not
completed work. Early `session.start` is accepted as complementary evidence;
conflicting early/terminal IDs fail the call and suppress receipt lookup and
normal reconciliation for that conflicting row. No permission or turn-cap
flags change.

Reconciliation retains conflicting calls as unresolved without preventing
unrelated calls from using their own receipts. A row's existing session ID
cannot override a conflicting journal or completion event. Nested `agentId`
filtering is Copilot-specific; Pi `message_end` events still count toward live
dollar/token caps when they carry an agent ID.

Older CLIs without demonstrated flag support are refused before cold dispatch;
Argus does not silently fall back to an unbound metered call. A timed-out help
probe also refuses; it does not imply a provider charge. Typed capability or
identity-argument preparation refusals are recorded as denied/not-billed because
no provider process was started; this never classifies cancelled metered work. Low-level embedders
using AgentCliRunner without an accounting callback retain their existing API
and do **not** gain a durable-accounting guarantee. Warm ACP keeps its existing
protocol and is outside this cold-CLI fix's guarantee.

Pre-dispatch refusal currently still consumes the existing provider call quota;
this is a known limitation, not a billed provider call. A generic startup
exception is not proof of zero provider work: process ownership setup can fail
after process creation, so uncertain failures retain
their binding and unknown liability. The identity journal currently rewrites
an atomic snapshot under the ledger lock; large-history compaction and a
transactional quota-refund protocol are not implemented by this change.

This prevents the demonstrated first-call identity loss but does not implement
historical exact-evidence repair, certify cancelled-tail completeness, or settle
a runtime campaign. Existing priced receipts do not establish that a cancelled
request cannot receive a later charge. Historical repair and explicit
accounting-pending projection are a separate draft. No live-provider execution
or Windows durability validation is claimed; tests use synthetic process/store
fixtures. An independent accounting-design review remains required.
