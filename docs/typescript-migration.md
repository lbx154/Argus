# TypeScript runtime

Argus runs on its TypeScript runtime across three workspace packages:

- `packages/contracts` owns the event payload schemas, WebAPI protocol constants,
  snapshot/project/cost/decision wire types, and API compatibility checks.
  `frontend/core` re-exports the shared definitions for the web cockpit.
- `packages/runtime` owns bounded child-process execution (`executeProcess`),
  reference pricing (`model_pricing.json`), usage ledger summarization, and the
  Pi JSON-stream transport utilities.
- `packages/core` (`@lbx154/argus`) owns the role orchestration flow, CLI
  backends (`codex`, `claude`, `copilot`, `pi`), bubblewrap sandboxing, git
  worktree isolation, persistence, and the web cockpit server.

Node **22.12+** is required:

```sh
npm ci
npm run check
```

After changing a contract source:

```sh
npm run contracts:generate
npm run contracts:check
```


For the optional native CLI integration check, explicitly select a compatible
Argus Pi executable. It uses temporary agent configuration and localhost
provider responses, with no paid model requests:

```sh
ARGUS_PI_TEST_CLI=/absolute/path/to/argus-pi python -m pytest \
  tests/core/test_typescript_pi_native.py -o addopts='' -q
```

## Usage and pricing

`extractTokenUsage(events)` and the constant-memory `TokenUsageAccumulator`
preserve Python's source precedence and field-presence flags: cumulative Codex
usage replaces earlier tuples; Claude, Copilot, Pi and OpenCode deltas are added
according to their existing provider formats. Explicit zero remains distinct
from absent usage. Claude request-unit placeholders and Pi empty-error sentinels
retain their existing interpretation.

`quoteTokenUsage(model, counts)` ports strict per-call pricing, including cache
reads/writes, reasoning output and the long-context threshold. Unknown models
stay unpriced. `quoteCopilotUsage` covers legacy premium requests only; modern
Copilot AIU billing, receipt deduplication and reconciliation remain Python.

Each Pi `result.accounting` has `usage`, `pricing` and `error`. The adapter prices
each assistant `message_end` separately, using its reported model/provider when
available and the requested model/provider otherwise. A turn's reported cost
takes precedence; missing costs use the shared reference table. Summing these
per-turn quotes avoids applying a long-context multiplier just because the
whole conversation crossed the threshold. The raw `usage.provider_cost_usd`
contains only observed provider totals and may cover fewer turns than `pricing`.

Missing turn usage, unknown prices, cancellation and unsuccessful settlement
leave a partial/unpriced quote and preserve any known cost. These observations
are estimates, not durable settlements or permission to spend. The production
Python accounting path remains unchanged.

Node counts and their sums must fit nonnegative safe integers. Out-of-range
counts or overflowing cost sums raise `UsageAccountingError` in the standalone
helpers. Pi catches that error and returns null usage/cost with a partial quote
and a diagnostic, alongside the normal transport receipt. It never silently
rounds oversized counts. The TS premium-request override also rejects infinite
rates. These are explicit numeric boundaries versus Python's arbitrary-size
integers; finite supported inputs are checked against Python.

The Node suite and Python suite read shared usage/pricing fixtures. Python CI
also compares generated streams (including every prefix) and threshold cases
against the built Node implementation. Real subprocess tests exercise multi-turn
pricing, partially reported costs, model changes, interruption and overflow:

```sh
npm run check
python -m pytest tests/core/test_typescript_accounting.py
```

For normalized ledger summaries, model-usage rows replace their parent record's
token/cost totals when present. The first `(session_id, usage_event_id)` wins
across overlapping receipts; rows without a complete identity are not deduplicated.
Call counts, pricing-status counts and premium-request metadata still count all
records. Partial/unpriced summaries with no positive known cost retain a null
total. Invalid inputs, unsafe integer sums and capacity limits latch an error;
that accumulator cannot later produce a successful summary.

Summary parity and real HTTP/storage checks run with:

```sh
python -m pytest tests/core/test_typescript_usage_summary.py \
  tests/webapi/test_read_bridge.py tests/webapi/test_typescript_read_api.py
```

## State ownership during migration

| State | Current writer | TS behaviour in this step |
| --- | --- | --- |
| Backlog live/archive/commit files | Python `LifeMemory` | Read through the Python query bridge |
| Event journal and mission projections | Python event sink | Read through Python; no TS writes |
| Continuous configuration and daemon controls | Python daemon | Snapshot reads through Python; no commands |
| Usage ledger (`usage.jsonl`) | Python daemon | Node project-cost fold and Pi quotes; no TS writes |
| Pipeline stages, Manager session and verdict outbox | Python orchestration | No access |
| Explicit standalone Pi session directory | The invoked Pi process | Caller controls access |

Never run a Python and TS writer against the same Argus state as a parity test.
Use copied fixtures and isolated temporary directories. State takeover requires
draining the old owner, a consistent backup, cross-process locking compatibility,
crash-recovery checks and a rollback plan that accounts for newly incurred costs.

Retired Python helpers can be removed after checking callers, registered hooks
and build entrypoints. The unused Python event-generator rendering API and old
runtime helper paths have been removed. The generator's `python -m` CLI remains
because the Web and release builds invoke it. Python pricing, Pi transport,
event validation and storage are still used by the production daemon; a TS
implementation existing alongside them does not yet make those Python paths
unused.

## Next increments

1. Extend shared contracts to write commands, port read projections into Node
   where they can preserve storage semantics, and introduce frontend support for
   the explicit read-only profile. Snapshot types and the read API are in place.
2. Verify the Pi adapter against a configured external provider and complete
   remaining production capabilities, especially training capture and role
   environment/session ownership. Native schemas, tool plugins and turn
   allowances now have local CLI coverage; daemon/client separation remains a
   required integration boundary.
3. Port storage and budget primitives with fault injection (usage parsing and
   reference pricing and ledger cost projections are now available), then complete one
   Planner → Engineer → Reviewer → Manager task with restart/cancellation tests.
4. Expand to continuous scheduling, remaining CLI backends and write APIs.
5. Add Python plugin workers and converge installation/desktop delivery.

The current change does not claim production runtime parity or a majority-code
migration. These increments are accepted by behaviour, compatibility and recovery
evidence; old implementations are removed when their replacements take ownership.
