# Daemon Command Protocol

Lifecycle commands are durable and idempotent. WebAPI and CLI operations write
to `daemon.commands.jsonl` and maintain authoritative state in
`daemon.command-state.json` under a cross-process lock.

Each command contains:

- `command_id`: client-generated idempotency key.
- `operation`: `create`, `start`, `stop`, `drain`, `kill`, or `replace`.
- `expected_revision`: optional optimistic-concurrency fence.
- `args`: operation parameters.
- `status`: `accepted`, `running`, `applied`, `failed`, or `rejected`.
- `revision`: monotonically increasing command-state revision.
- durable result/error ACK.

Submitting an existing `command_id` returns its stored receipt and never runs the
handler twice. A stale `expected_revision` is durably rejected before any side
effect. Concurrent duplicate requests have one claimant; other callers observe
`running` and can poll the command state.

Project snapshots expose `daemon_commands.revision` and recent receipts. Web and
TUI lifecycle actions send the snapshot revision, preventing a stale control
surface from overwriting newer daemon state.
