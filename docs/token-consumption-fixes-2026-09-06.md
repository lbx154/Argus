# Token consumption fixes

Argus could repeat unchanged paper assessments, replay duplicate operator rules,
and lose Reviewer session reuse after writing its own REVIEW.md. Copilot usage
could also be read from the personal database while the actual child wrote to
the Argus-owned database, making token-billed calls appear to cost one premium
request. These changes address those concrete triggers.

## Behavior

- Identical standing directives are projected once, retaining the latest valid
  revision. Adjacent identical retries are idempotent. Audit history, scope,
  role applicability, revocations, and independent once/bounded actions remain.
- Changing research context and REVIEW.md are carried in Reviewer deltas;
  stable role instructions can retain the existing session.
- Successful Visual and ColdRead assessments can be reused for the same exact
  PDF, prompt, model, and policy. They run in isolated PDF workspaces. The cache
  is host memory, clears on restart, and does not cache scientific judgments or
  integrated verdicts. Identical before/after manuscript snapshots skip the
  semantic-loss comparison only after both snapshot trees are rehashed.
- Usage cursors select the same effective Copilot home as execution. Personal
  fallback queries are bounded to the exact session and their own start cursor.
  Missing modern telemetry remains pending; only an affirmative readable
  legacy schema permits premium-only pricing. Late database/WAL writes trigger
  idempotent reconciliation, including before budget admission.
- The default global daily cap is **$1,000**. Explicit persisted or environment
  overrides still work. The deployed host configuration is also set to $1,000.
- Running Copilot sessions publish observed costs, including nested agents,
  about every five seconds. New admissions and running calls check settled
  plus observed costs. Known spend survives a worker crash until settlement or
  day rollover. Unknown-cost `block`/`allow` policy is now honored.
- Warm ACP calls emit their parent session identity before execution so live
  accounting works for the first session as well as resumed calls.
- Explicit startup policy/model-discovery refusals with no metering evidence
  are unbilled. The same diagnostic after metered work retains its charges;
  generic transport failures remain unknown. Concrete startup diagnostics
  are preserved instead of being replaced by a generic process-exit message.

The daily cap uses the existing local-day settlement convention. Provider
telemetry and cancellation are asynchronous: an already running request and
the polling interval can produce a small overshoot. No claim of a perfectly
instantaneous provider-side hard limit is made.

## Validation

- 494 related regression tests passed across usage, Copilot CLI/ACP,
  reconciliation, operator context, review, role sessions, and configuration.
- Repository-wide Ruff and `git diff --check` passed.
- A real local subprocess using the Copilot stream and SQLite contract was
  cancelled after parent and nested-agent reported costs reached $1,000.
  Unrelated sessions were excluded. Tests also cover policy resolution,
  crashes, midnight reset, external project ledgers, and late WAL reconciliation.
- On the deployed host, an admission against the real over-budget daily ledger
  was refused with the configured $1,000 cap. Historical standing rules were
  projected without duplicate standing copies.

## Real execution

A bounded software task exercised the actual SkillLoop with an Engineer and
independent Reviewer using the host's existing Codex CLI/provider configuration.
The Engineer repaired a USD amount parser without editing the supplied tests;
the Reviewer returned `done`. All five acceptance tests passed. Both provider
calls completed and persisted token-based costs:

| Role | Input (including cache) | Cached input | Output | Ledger estimate |
|---|---:|---:|---:|---:|
| Engineer | 61,793 | 50,306 | 1,721 | $0.163948 |
| Reviewer | 55,448 | 52,043 | 1,246 | $0.0941365 |

The $1,000 host budget remained enabled during this execution. Native Copilot
live validation was also attempted, but its model-discovery endpoint returned
policy/HTTP 421 errors before inference. This is an external acceptance limit;
it is not reported as a successful native Copilot execution. Copilot accounting
was additionally verified against real historical SQLite usage and targeted
regressions, including the wrong-database and delayed-WAL cases.
