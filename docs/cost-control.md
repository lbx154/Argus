# Atomic Cost Control

Argus has one monetary limit: the host-global daily USD cap. It uses two
durable accounting layers:

- `projects/<id>/usage.jsonl` is the authoritative settled call ledger.
- `cost-control.json` protects budget that is currently in flight between
  provider spawn and usage settlement.

## Call lifecycle

1. Before `run_exec` starts a provider process, Argus locks
   `cost-control.lock`.
2. It reads settled usage and active reservations across every project, then
   reserves one call-sized hold from the remaining host-global daily USD.
3. After the call, Argus persists exactly one `UsageRecord` and atomically
   removes the reservation.
4. The settlement audit is appended to `cost-control.jsonl` and the project
   `events.jsonl` timeline.

Concurrent daemons cannot reserve the same global remainder. Dead-process
reservations are pruned; completed calls still count because usage is persisted
before reservation removal. Reservations are accounting holds only: Argus does
not translate them into provider-specific credits or token fences.

## Unknown prices

A completed call whose cost is `partial`, `unpriced`, or unavailable is stored
in the `unresolved` set. The default policy is fail-closed: new provider calls
are denied until reconciliation produces a priced `UsageRecord`.

Set `ARGUS_SKILL_UNPRICED_COST_POLICY=allow` only when an operator deliberately
accepts unknown monetary exposure. Unknown usage is never converted to `$0`.

## Operator surface

Snapshots expose host-global settled USD, the global cap, active reservation
count, reserved USD, unresolved call count, and policy. TUI and Web cost gauges
show those global values directly.

The reservation is not a provider-side token or credit limit. A call may cost
more than its hold; the actual USD is still settled into the global ledger and
the overrun remains visible in the settlement audit.
