# Live Experiment Protocol

> Current long-running-work contract. See
> [`DESIGN_AUTHORITY.md`](DESIGN_AUTHORITY.md). The canonical launcher is
> `python -m argus_skill.tools.subagent`; raw background-shell recipes are not
> the Argus supervision contract.

Any long experiment must be observable, interruptible, attributable to one
registry id, and safe for the Engineer to leave running while it performs other
work.

## 1. Launch rule

GPU work and commands expected to run longer than roughly two minutes must be
submitted through the unified subagent tool:

```bash
python -m argus_skill.tools.subagent submit \
  --task-id <stable-id> \
  --description "<what this run tests>" \
  --command '<real command>' \
  --mode supervised \
  --run-dir <run-dir> \
  --cwd <project-root> \
  --timeout <seconds> \
  --monitor-interval <seconds>
```

Use `--mode direct` only when periodic model supervision is unnecessary and the
command still has an external, inspectable completion contract. CPU-exclusive
jobs must also declare `--cpu-count N` or `--cpu-ids i,j`.

Do not keep an Engineer turn alive with raw `bash`, a shell `while/sleep` loop,
or repeated polling. Do not create a second ad-hoc PID registry beside
`.argus_subagents/`.

## 2. Run-directory contract

When `--run-dir` is supplied, the experiment itself should expose at least:

| File | Purpose |
| --- | --- |
| `manifest.json` | immutable objective, command/config, data/model identity, expected work and budget |
| `status.json` | latest state, counters, current item and timestamps |
| `progress.jsonl` | append-only meaningful lifecycle/progress events |
| `stdout.log` / `stderr.log` | complete worker output, when the runner does not provide equivalent logs |
| `STOP` | run-scoped cancellation flag watched by the worker |

Result-producing experiments should stream partial rows to a run-local result
file as soon as each atomic trial finishes. Exact filenames may be project
specific, but `status.json` and `progress.jsonl` are the standard signals read by
the supervised subagent path.

The launcher refuses a stale `<run-dir>/STOP` by default because it would poison
the new run. `--clear-stop` is an explicit operator/Engineer decision, not a
silent cleanup.

## 3. Progress durability

For every meaningful event or result row:

1. write a complete JSON line;
2. flush the file;
3. use `os.fsync` for long or expensive runs;
4. update `status.json` atomically with temp file + `os.replace`.

Suggested progress events include `run_started`, `trial_started`, `trial_done`,
`trial_failed`, `heartbeat`, `early_stop`, `run_completed`, `run_failed`, and
`run_cancelled`. Project-specific events are allowed when their meaning is
documented.

## 4. Cancellation and early stop

The worker must check `<run-dir>/STOP` before expensive calls and at a bounded
cadence. On cancellation it should:

1. stop launching new work;
2. finish the current atomic persistence operation;
3. write the terminal progress event;
4. update `status.json`;
5. exit nonzero or with the project's documented cancellation code.

In supervised mode, a non-empty supervisor concern is a real stop decision: the
subagent writes `STOP`, terminates the worker, persists the concern, and opens a
discussion. The Engineer must inspect the concern and use
`python -m argus_skill.tools.subagent reply` when the supervisor is waiting; it
must not blindly relaunch the same configuration.

## 5. Engineer wait/yield contract

The Engineer should continue independent work while a healthy supervised job is
running. When no independent work remains, it may request one supervisor-cadence
yield by making the final non-empty response line exactly:

```json
{"wait_for":"subagent","wait_id":"<task-id>"}
```

`engineer/round_waits.py` validates that the id exists, belongs to a supervised
self-watched job, and is currently waitable. It then sleeps only for that job's
bounded monitor cadence and wakes early on a state transition. Unknown, stale,
direct, degrading or discussion-blocked jobs do not receive a blind wait.

The same shape with `wait_for="external_work"` applies to other entries in the
unified external-work registry.

## 6. Inspecting work

```bash
python -m argus_skill.tools.subagent status --task-id <task-id>
python -m argus_skill.tools.subagent list
python -m argus_skill.tools.subagent wait --task-id <task-id>
```

Status and reports must be based on durable registry/run files, not private
process memory. This lets the cockpit, Reviewer and another shell inspect the
same facts after the launching Engineer turn has ended.

