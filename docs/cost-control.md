# Host-Global Cost Control

Argus has one monetary limit: the host-global daily USD cap. It uses two
durable accounting layers:

- `projects/<id>/usage.jsonl` is the authoritative settled call ledger.
- `cost-control.json` records in-flight calls and unresolved prices so all
  daemons apply one admission policy.

## Call lifecycle

1. Before `run_exec` starts a provider process, Argus locks
   `cost-control.lock`.
2. It reads settled usage across every project and denies the call only when
   that host-global daily total has reached the cap.
3. After the call, Argus persists exactly one `UsageRecord` and atomically
   removes the zero-dollar in-flight admission record.
4. The settlement audit is appended to `cost-control.jsonl` and the project
   `events.jsonl` timeline.

There is no fixed per-call or control-plane USD hold. Concurrent calls may be in
flight together; each new call is checked against globally settled spend.
Dead-process admission records are pruned, and completed calls count through the
usage ledger.

## Unknown prices

A completed call whose cost is `partial`, `unpriced`, or unavailable is stored
in the `unresolved` set. The default policy is fail-closed: new provider calls
are denied until reconciliation produces a priced `UsageRecord`.

Set `ARGUS_SKILL_UNPRICED_COST_POLICY=allow` only when an operator deliberately
accepts unknown monetary exposure. Unknown usage is never converted to `$0`.

## Operator surface

Snapshots expose host-global settled USD, the global cap, in-flight call count,
unresolved call count, and policy. TUI and Web cost gauges show those global
values directly.
