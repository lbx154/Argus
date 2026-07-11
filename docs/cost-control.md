# Atomic Cost Control

Argus uses two durable accounting layers:

- `projects/<id>/usage.jsonl` is the authoritative settled call ledger.
- `cost-control.json` protects budget that is currently in flight between
  provider spawn and usage settlement.

## Call lifecycle

1. Before `run_exec` starts a provider process, Argus locks
   `cost-control.lock`.
2. It reads settled usage and active reservations, then reserves the maximum
   amount still available under the mission, project-day, and global-day caps.
3. After the call, Argus persists exactly one `UsageRecord` and atomically
   removes the reservation.
4. The settlement audit is appended to `cost-control.jsonl` and the project
   `events.jsonl` timeline.

Concurrent daemons cannot reserve the same remaining amount. Dead-process
reservations are pruned; completed calls still count because usage is persisted
before reservation removal.

## Unknown prices

A completed call whose cost is `partial`, `unpriced`, or unavailable is stored
in the `unresolved` set. The default policy is fail-closed: new provider calls
are denied until reconciliation produces a priced `UsageRecord`.

Set `ARGUS_SKILL_UNPRICED_COST_POLICY=allow` only when an operator deliberately
accepts unknown monetary exposure. Unknown usage is never converted to `$0`.

## Operator surface

Snapshots expose active reservation count, reserved USD, unresolved call count,
and policy. TUI and Web cost gauges show non-zero reservations and unresolved
pricing beside settled spend.

The reservation is a budget envelope, not a provider-side token limit. The
execution gateway must also apply model/token limits when the provider supports
them; reservation overruns remain visible in the settlement audit.
