# Copilot cold-CLI session binding (Refs #129, partial prevention)

The accounting-enabled AgentCliBackend now installs a per-call binding callback.
After the ACP routing decision, and before cold subprocess spawn, the runner:

1. Rejects identity selectors in default/per-call extra arguments (`--session-id`,
   `--resume`, `--continue`, including equals forms).
2. Preserves an existing resume identity. For a new session only, probes the
   selected executable's `--help` for `--session-id`, then allocates a UUID.
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

Older CLIs without demonstrated flag support are refused before cold dispatch;
Argus does not silently fall back to an unbound metered call. A timed-out help
probe also refuses; it does not imply a provider charge. Typed capability or
identity-argument preparation refusals are recorded as denied/not-billed because
no provider process was started; this never classifies cancelled metered work. Low-level embedders
using AgentCliRunner without an accounting callback retain their existing API
and do **not** gain a durable-accounting guarantee. Warm ACP keeps its existing
protocol and is outside this cold-CLI fix's guarantee.

This prevents the demonstrated first-call identity loss but does not implement
historical exact-evidence repair, certify cancelled-tail completeness, or settle
a runtime campaign. Existing priced receipts do not establish that a cancelled
request cannot receive a later charge. Historical repair and explicit
accounting-pending projection are a separate draft. No live-provider execution
or Windows durability validation is claimed; tests use synthetic process/store
fixtures. An independent accounting-design review remains required.
