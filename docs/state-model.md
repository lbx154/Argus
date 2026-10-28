# State model

Decision record for ROADMAP L3.1 (state model) and L3.4 (storage). Measurements behind it:
[docs/audits/python-complexity-2026-10-28.md](audits/python-complexity-2026-10-28.md), section 8.

## Rule

Every fact has one owning record. A summary of other records is computed when it is read and is not
written to disk; a process may keep what it computed in memory and update it from the bytes appended
since. Where a prompt names a file, that file is an owning record (or a stable name for one, see
`handoffs/<id>/latest.json` below), so a role reads the same facts the host does.

## Storage (L3.4)

Plain files in the project state directory, as today:

- append-only JSONL logs (`events.jsonl` with its retained generations, `backlog.jsonl`, `usage.jsonl`,
  `operator_context.jsonl`), appended under their own lock;
- small JSON records replaced whole with `core.atomic_replace.write_atomic`.

No database for project state: roles read these files with their own tools, and the Python runtime
stays. The two existing SQLite stores (the operator inbox protocol and the failure-experience index)
are left as they are.

## Owners

| Fact | Owning record |
| --- | --- |
| What happened: role turns, verdicts, mission lifecycle, delivered completions | `events.jsonl` (+ `events.jsonl.N`) |
| Tasks and their current status and result | `backlog.jsonl` |
| A mission's contract and each round's handoff letter | `handoffs/<id>/mission.json`, `handoffs/<id>/round-*.json` |
| The continuous objective | `continuous.json` |
| Pipeline stage | `.argus/PIPELINE_STATE.json` |
| Manager supervision decisions | `manager-supervision/<id>.json` |
| Operator directives and messages | `operator_context.jsonl` |
| Model calls and their cost | `usage.jsonl` |
| Each circuit breaker's state (counts, windows, pauses) | its own file: `runtime-failure-circuit.json`, `planner-failure-backoff.json`, `completion-rejection-circuit.json` |

The circuit files are owners, not copies: their counters and holds are recorded nowhere else, and
the events that mention them are notifications written from them. Recounting them from events would
make two records of one circuit, so they stay as they are.

## Computed, not stored

| Summary | Computed from | Was stored as |
| --- | --- | --- |
| Mission View (cockpit read model) | `events.jsonl` | `mission-view.json` |
| Which completions the log already holds | `events.jsonl` | `mission-events.index.jsonl` |
| Newest supervision decision | newest `manager-supervision/<id>.json` | `manager-supervision/latest.json` |
| A mission's latest work | newest `handoffs/<id>/round-*.json`, else `mission.json` | read from `handoffs/<id>/latest.json` |

`handoffs/<id>/latest.json` is still written: prompts name it, and a stable name keeps the
role-state block identical between turns. It holds only a reference to the newest record and the
host never reads it. Files of the old kinds left by earlier versions are ignored, not migrated.

`tests/test_derived_state_prompts.py` checks the prompt texts against those recorded before this
change; the only differences are the removed copies in reference lists, and the Manager citing
`events.jsonl` instead of `mission-view.json`.
